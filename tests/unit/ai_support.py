"""Shared deterministic fakes and payload builders for the AI module tests.

The FakeTransport is the private transport seam: it records every request and
replays scripted outcomes without any provider network call.
"""

import decimal
import json
from typing import Any

from apps.ai import transport
from apps.ai.conf import FeatureConfig

SOURCE = "Jane Doe is skilled in Node.js and Rust."


def feature_config(**overrides: object) -> FeatureConfig:
    values: dict[str, object] = {
        "feature": "candidate_profile_extraction",
        "model": "acme/profile-model",
        "base_url": "https://openrouter.example/api/v1",
        "api_key": "test-key",
        "deadline_seconds": 60.0,
        "max_attempts": 2,
        "max_input_tokens": 60_000,
        "max_output_tokens": 8_000,
        "price_ceiling": decimal.Decimal("0.50"),
        "account_cost_ceiling": decimal.Decimal("5.00"),
    }
    values.update(overrides)
    return FeatureConfig(**values)  # type: ignore[arg-type]


def completion_body(
    *,
    model: str = "acme/route-model",
    content: str = '"ok"',
    finish_reason: str = "stop",
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    cost: float | None = 0.02,
    provider: str | None = "openrouter",
    error: object | None = None,
    choice_error: object | None = None,
) -> bytes:
    usage: dict[str, object] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }
    if cost is not None:
        usage["cost"] = cost
    choice: dict[str, object] = {"finish_reason": finish_reason, "message": {"content": content}}
    if choice_error is not None:
        choice["error"] = choice_error
    body: dict[str, object] = {"model": model, "usage": usage, "choices": [choice]}
    if provider is not None:
        body["provider"] = provider
    if error is not None:
        body["error"] = error
    return json.dumps(body).encode()


def http_result(body: bytes, status: int = 200, headers: dict[str, str] | None = None):
    return transport.TransportResult(status=status, headers=headers or {}, body=body)


class FakeTransport:
    """Deterministic transport fake: scripted outcomes, recorded calls."""

    def __init__(self, *outcomes: object) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[tuple[Any, float]] = []

    def post_json(self, payload: Any, *, timeout: float) -> transport.TransportResult:
        self.calls.append((payload, timeout))
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, bytes):
            return transport.TransportResult(status=200, headers={}, body=outcome)
        assert isinstance(outcome, transport.TransportResult)
        return outcome

    @property
    def call_count(self) -> int:
        return len(self.calls)


def profile_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "full_name": "Jane Doe",
        "professional_title": None,
        "professional_summary": None,
        "phone_number": None,
        "location": None,
        "contact_email": None,
        "experiences": [],
        "educations": [],
        "projects": [],
        "skills": [],
        "languages": [],
    }
    payload.update(overrides)
    return payload


def posting_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "company_name": None,
        "company_website": None,
        "role_title": None,
        "job_description": None,
        "location": None,
        "compensation": None,
        "posting_url": None,
        "requirements": [],
    }
    payload.update(overrides)
    return payload
