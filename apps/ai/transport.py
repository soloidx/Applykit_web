"""The private OpenRouter transport.

This module is internal to the AI capability module. Provider objects, raw
JSON, and raw errors never cross the AI boundary: callers inside the module
receive either a normalized ``Completion`` or a ``TransportFailure`` carrying
only a fixed safe category and retry classification.

Every request is non-streaming strict JSON Schema with ``require_parameters``,
zero data retention, data-collection denial, a bounded output size, and the
configured per-request price ceiling.
"""

import datetime
import decimal
import email.utils
import json
import socket
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from apps.ai.conf import FeatureConfig
from apps.ai.errors import (
    CONFIGURATION_ERROR,
    INVALID_RESPONSE,
    PRIVACY_UNAVAILABLE,
    RATE_LIMITED,
    TIMEOUT,
    UNAVAILABLE,
)

__all__ = [
    "MAX_BODY_BYTES",
    "Transport",
    "TransportResult",
    "TransportTimeout",
    "TransportNetworkError",
    "TransportFailure",
    "Completion",
    "make_client",
    "build_payload",
    "classify_response",
    "interpret_response",
    "parse_retry_after",
]

MAX_BODY_BYTES = 2 * 1024 * 1024

_PRICING_DECIMALS = 12

# OpenRouter typed error codes that mean the privacy route excluded every
# endpoint. These are never retried and never relaxed.
_PRIVACY_CODES = frozenset({"privacy_restricted", "constraint_filtered", "data_collection"})
_AUTH_CODES = frozenset(
    {
        "authentication",
        "authentication_error",
        "invalid_api_key",
        "unauthorized",
        "forbidden",
        "401",
        "403",
    }
)
_PAYMENT_CODES = frozenset({"payment", "payment_required", "insufficient_credits", "402"})
_OVERSIZE_CODES = frozenset(
    {"payload_too_large", "request_too_large", "context_length_exceeded", "413"}
)
_VALIDATION_CODES = frozenset(
    {"validation", "validation_error", "malformed_request", "invalid_request", "400", "422"}
)
_POLICY_CODES = frozenset({"policy", "moderation", "content_filter", "refusal"})
_RATE_CODES = frozenset({"rate_limit", "rate_limit_exceeded", "429"})
_TIMEOUT_CODES = frozenset({"timeout", "request_timeout", "408"})
_SERVER_CODES = frozenset(
    {"server_error", "provider_overload", "overloaded", "upstream_error", "availability"}
)


@dataclass(frozen=True)
class TransportResult:
    status: int
    headers: Mapping[str, str]
    body: bytes


class TransportTimeout(Exception):
    """The transport timed out inside the remaining time budget."""


class TransportNetworkError(Exception):
    """The request failed before a complete response was received."""


class TransportFailure(Exception):
    """A provider-side failure expressed as a fixed safe category."""

    def __init__(self, category: str, *, retryable: bool, retry_after: float | None = None):
        super().__init__(category)
        self.category = category
        self.retryable = retryable
        self.retry_after = retry_after


@dataclass(frozen=True)
class Completion:
    model: str
    route: str | None
    content: str
    prompt_tokens: int
    completion_tokens: int
    cost: decimal.Decimal | None


class Transport(Protocol):
    def post_json(self, payload: Mapping[str, Any], *, timeout: float) -> TransportResult: ...


class UrllibTransport:
    """The OpenRouter-compatible Chat Completions HTTP client."""

    def __init__(self, base_url: str, api_key: str) -> None:
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key

    def post_json(self, payload: Mapping[str, Any], *, timeout: float) -> TransportResult:
        request = urllib.request.Request(
            self._url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return TransportResult(
                    status=response.status,
                    headers=_header_dict(response.headers),
                    body=response.read(MAX_BODY_BYTES + 1),
                )
        except urllib.error.HTTPError as error:
            return TransportResult(
                status=error.code,
                headers=_header_dict(error.headers),
                body=error.read(MAX_BODY_BYTES + 1),
            )
        except urllib.error.URLError as error:
            if isinstance(error.reason, TimeoutError | socket.timeout):
                raise TransportTimeout from None
            raise TransportNetworkError from None
        except TimeoutError:
            raise TransportTimeout from None
        except OSError:
            raise TransportNetworkError from None


def make_client(config: FeatureConfig) -> Transport:
    """The client factory: builds the private transport for one feature."""

    return UrllibTransport(config.base_url, config.api_key)


def build_payload(
    *,
    model: str,
    system_prompt: str,
    user_text: str,
    schema_name: str,
    schema: Mapping[str, Any],
    max_output_tokens: int,
    price_ceiling: decimal.Decimal,
) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": schema},
        },
        "provider": {
            "require_parameters": True,
            "data_collection": "deny",
            "zdr": True,
            "max_price": float(price_ceiling),
        },
        "max_tokens": max_output_tokens,
        "stream": False,
    }


def classify_response(result: TransportResult) -> TransportFailure | None:
    """Return the typed failure for a non-success response, or None for 2xx."""

    if 200 <= result.status < 300:
        return None
    failure = _typed_error_failure(result)
    if failure is not None:
        return failure
    retry_after = parse_retry_after(result.headers)
    if result.status == 429:
        return TransportFailure(RATE_LIMITED, retryable=True, retry_after=retry_after)
    if result.status == 408 or 500 <= result.status <= 599:
        return TransportFailure(UNAVAILABLE, retryable=True, retry_after=retry_after)
    if result.status in (401, 402, 403, 400, 413, 422):
        return TransportFailure(CONFIGURATION_ERROR, retryable=False)
    return TransportFailure(UNAVAILABLE, retryable=False)


def interpret_response(result: TransportResult) -> Completion:
    """Normalize a successful HTTP response into a Completion.

    Raises TransportFailure(INVALID_RESPONSE) for any response whose body
    cannot be fully trusted: oversized bodies, malformed JSON, embedded
    errors, absent usage, missing content, and unverified finish reasons.
    """

    if len(result.body) > MAX_BODY_BYTES:
        raise TransportFailure(INVALID_RESPONSE, retryable=False)
    try:
        decoded = json.loads(result.body)
    except ValueError, UnicodeDecodeError:
        raise TransportFailure(INVALID_RESPONSE, retryable=False) from None
    if not isinstance(decoded, dict):
        raise TransportFailure(INVALID_RESPONSE, retryable=False)

    error_failure = _typed_error_failure_from_value(decoded.get("error"))
    if error_failure is not None:
        raise error_failure

    model = decoded.get("model")
    provider = decoded.get("provider")
    usage = decoded.get("usage")
    choices = decoded.get("choices")
    if not isinstance(model, str) or not model.strip():
        raise TransportFailure(INVALID_RESPONSE, retryable=False)
    if provider is not None and (not isinstance(provider, str) or not provider.strip()):
        raise TransportFailure(INVALID_RESPONSE, retryable=False)
    if not isinstance(usage, dict) or not _usable_usage(usage):
        raise TransportFailure(INVALID_RESPONSE, retryable=False)
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise TransportFailure(INVALID_RESPONSE, retryable=False)

    choice = choices[0]
    finish_reason = choice.get("finish_reason")
    if finish_reason == "error":
        error_failure = _typed_error_failure_from_value(choice.get("error"))
        raise error_failure or TransportFailure(INVALID_RESPONSE, retryable=False)
    if finish_reason not in ("stop", "length"):
        raise TransportFailure(INVALID_RESPONSE, retryable=False)

    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise TransportFailure(INVALID_RESPONSE, retryable=False)

    return Completion(
        model=model,
        route=provider,
        content=content,
        prompt_tokens=int(usage["prompt_tokens"]),
        completion_tokens=int(usage["completion_tokens"]),
        cost=_cost_value(usage.get("cost")),
    )


def parse_retry_after(headers: Mapping[str, str]) -> float | None:
    """Parse a Retry-After header into seconds within the caller's budget."""

    value = headers.get("Retry-After") or headers.get("retry-after")
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        retry_at = email.utils.parsedate_to_datetime(value)
    except TypeError, ValueError, OverflowError:
        return None
    if retry_at is None:
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=datetime.UTC)
    remaining = (retry_at - datetime.datetime.now(datetime.UTC)).total_seconds()
    return max(0.0, remaining)


def _header_dict(headers: Any) -> dict[str, str]:
    if headers is None:
        return {}
    return {str(key): str(value) for key, value in headers.items()}


def _typed_error_failure(result: TransportResult) -> TransportFailure | None:
    try:
        decoded = json.loads(result.body)
    except ValueError, UnicodeDecodeError:
        return None
    if not isinstance(decoded, dict):
        return None
    return _typed_error_failure_from_value(decoded.get("error"))


def _typed_error_failure_from_value(error: Any) -> TransportFailure | None:
    if not isinstance(error, dict):
        return None
    code = str(error.get("code", "")).strip().lower()
    retry_after = _error_retry_after(error)

    if code in _PRIVACY_CODES:
        return TransportFailure(PRIVACY_UNAVAILABLE, retryable=False)
    if code in _AUTH_CODES or code in _PAYMENT_CODES:
        return TransportFailure(CONFIGURATION_ERROR, retryable=False)
    if code in _OVERSIZE_CODES or code in _VALIDATION_CODES:
        return TransportFailure(CONFIGURATION_ERROR, retryable=False)
    if code in _POLICY_CODES:
        return TransportFailure(UNAVAILABLE, retryable=False)
    if code in _RATE_CODES:
        return TransportFailure(RATE_LIMITED, retryable=True, retry_after=retry_after)
    if code in _TIMEOUT_CODES:
        return TransportFailure(TIMEOUT, retryable=True, retry_after=retry_after)
    if code in _SERVER_CODES or isinstance(error.get("retryable"), bool):
        retryable = bool(error.get("retryable", True))
        return TransportFailure(UNAVAILABLE, retryable=retryable, retry_after=retry_after)
    return TransportFailure(UNAVAILABLE, retryable=False)


def _error_retry_after(error: Mapping[str, Any]) -> float | None:
    metadata = error.get("metadata")
    if not isinstance(metadata, dict):
        return None
    raw = metadata.get("retry_after")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except TypeError, ValueError:
        return None


def _usable_usage(usage: Mapping[str, Any]) -> bool:
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    for value in (prompt_tokens, completion_tokens):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return False
    cost = usage.get("cost")
    if cost is not None and not isinstance(cost, int | float):
        return False
    return True


def _cost_value(cost: Any) -> decimal.Decimal | None:
    if cost is None:
        return None
    return decimal.Decimal(str(float(cost))).quantize(decimal.Decimal("0.000001"))
