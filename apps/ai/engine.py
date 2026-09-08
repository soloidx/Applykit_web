"""The private execution loop for one logical AI operation.

Runs at most the configured attempts inside the total deadline, retries only
the approved categories (transport failures, HTTP 408/429/5xx, and
schema-invalid output), and converts every failure into a fixed safe
category. Provider objects, prompts, source text, and raw outputs never
escape this module.
"""

import decimal
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar

from apps.ai import transport
from apps.ai.conf import FeatureConfig
from apps.ai.errors import INTERNAL_ERROR, INVALID_RESPONSE, TIMEOUT, UNAVAILABLE
from apps.ai.schemas import ResultRejected

__all__ = ["Usage", "EngineOutcome", "run_extraction"]

T = TypeVar("T")

_RETRY_DELAY_SECONDS = 1.0


@dataclass(frozen=True)
class Usage:
    """Aggregate usage across all attempts of one logical operation."""

    model: str
    route: str
    prompt_tokens: int
    completion_tokens: int
    cost: decimal.Decimal

    @classmethod
    def zero(cls) -> Usage:
        return cls("", "", 0, 0, decimal.Decimal("0"))


@dataclass(frozen=True)
class EngineOutcome[T]:
    ok: bool
    category: str | None
    extraction: T | None
    usage: Usage
    attempts: int


def run_extraction[T](
    config: FeatureConfig,
    *,
    payload: Mapping[str, Any],
    validate: Callable[[Any], T],
) -> EngineOutcome[T]:
    client = _client_for(config)
    usage = Usage.zero()
    attempts = 0
    deadline = _monotonic() + config.deadline_seconds

    for attempt in range(config.max_attempts):
        remaining = deadline - _monotonic()
        if remaining <= 0:
            return EngineOutcome(False, TIMEOUT, None, usage, attempts)

        attempts += 1
        result = _send(client, payload, remaining)
        outcome: EngineOutcome[T] | None = None
        if isinstance(result, transport.TransportFailure):
            failure: transport.TransportFailure | None = result
        else:
            failure = transport.classify_response(result)
            if failure is None:
                failure, usage, outcome = _evaluate(result, validate, usage, attempts)
        if outcome is not None:
            return outcome

        assert failure is not None
        if not failure.retryable:
            return EngineOutcome(False, failure.category, None, usage, attempts)
        if attempt + 1 >= config.max_attempts:
            return EngineOutcome(False, failure.category, None, usage, attempts)
        remaining = deadline - _monotonic()
        if remaining <= 0:
            continue
        if failure.retry_after is not None and failure.retry_after >= remaining:
            # The provider asked for more time than the total deadline has
            # left; a bounded sleep could not produce another attempt.
            return EngineOutcome(False, failure.category, None, usage, attempts)
        delay = failure.retry_after if failure.retry_after is not None else _RETRY_DELAY_SECONDS
        _sleep(min(delay, remaining))

    return EngineOutcome(False, TIMEOUT, None, usage, attempts)


def _send(
    client: transport.Transport, payload: Mapping[str, Any], timeout: float
) -> transport.TransportResult | transport.TransportFailure:
    """Run one transport attempt, normalizing every failure."""

    try:
        result = client.post_json(payload, timeout=timeout)
    except transport.TransportTimeout:
        return transport.TransportFailure(TIMEOUT, retryable=True)
    except transport.TransportNetworkError:
        return transport.TransportFailure(UNAVAILABLE, retryable=True)
    except transport.TransportFailure as raised:
        return raised
    except Exception:
        # Never let an unexpected transport error leak raw detail.
        return transport.TransportFailure(INTERNAL_ERROR, retryable=False)
    return result


def _evaluate[T](
    result: transport.TransportResult,
    validate: Callable[[Any], T],
    usage: Usage,
    attempts: int,
) -> tuple[transport.TransportFailure | None, Usage, EngineOutcome[T] | None]:
    """Interpret and locally validate one response.

    Returns the failure for the retry decision, the possibly-updated
    aggregate usage, and a terminal outcome. A None outcome with a retryable
    failure means the output was schema-invalid; a None outcome with a
    non-retryable failure is the terminal safe category.
    """

    try:
        completion = transport.interpret_response(result)
    except transport.TransportFailure as raised:
        return raised, usage, None

    usage = _aggregate(usage, completion)
    try:
        decoded = json.loads(completion.content)
    except ValueError:
        # Malformed JSON is not schema-invalid output and is never retried.
        return (
            transport.TransportFailure(INVALID_RESPONSE, retryable=False),
            usage,
            None,
        )
    try:
        extraction = validate(decoded)
    except ResultRejected:
        return (
            transport.TransportFailure(INVALID_RESPONSE, retryable=True),
            usage,
            None,
        )
    except Exception:
        return (
            transport.TransportFailure(INTERNAL_ERROR, retryable=False),
            usage,
            None,
        )
    return None, usage, EngineOutcome(True, None, extraction, usage, attempts)


def _aggregate(usage: Usage, completion: transport.Completion) -> Usage:
    cost = completion.cost if completion.cost is not None else decimal.Decimal("0")
    return Usage(
        model=completion.model or usage.model,
        route=completion.route or usage.route,
        prompt_tokens=usage.prompt_tokens + completion.prompt_tokens,
        completion_tokens=usage.completion_tokens + completion.completion_tokens,
        cost=usage.cost + cost,
    )


def _client_for(config: FeatureConfig) -> transport.Transport:
    return transport.make_client(config)


def _monotonic() -> float:
    return time.monotonic()


def _sleep(seconds: float) -> None:
    time.sleep(seconds)
