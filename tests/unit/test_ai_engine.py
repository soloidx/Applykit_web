import decimal
import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from ai_support import (
    SOURCE,
    FakeTransport,
    completion_body,
    feature_config,
    http_result,
    profile_payload,
)

from apps.ai import engine, schemas, transport
from apps.ai.conf import FeatureConfig
from apps.ai.engine import EngineOutcome, Usage
from apps.ai.errors import (
    CONFIGURATION_ERROR,
    INTERNAL_ERROR,
    INVALID_RESPONSE,
    PRIVACY_UNAVAILABLE,
    RATE_LIMITED,
    TIMEOUT,
    UNAVAILABLE,
)

pytestmark = pytest.mark.unit


def success_body(
    *,
    model: str = "acme/route-model",
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    cost: float | None = 0.02,
) -> bytes:
    return completion_body(
        model=model,
        content=json.dumps(profile_payload()),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost=cost,
    )


@dataclass
class Harness:
    now: float = 0.0
    sleeps: list[float] = field(default_factory=list)

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def run(
        self,
        *outcomes: object,
        config: FeatureConfig | None = None,
        validator: Any | None = None,
        client: Any | None = None,
    ) -> tuple[EngineOutcome[Any], FakeTransport]:
        fake = client if client is not None else FakeTransport(*outcomes)
        engine._client_for = lambda _config: fake  # type: ignore[method-assign]
        outcome = engine.run_extraction(
            config or feature_config(),
            payload={"model": "acme/profile-model"},
            validate=validator or (lambda decoded: schemas.parse_profile_result(decoded, SOURCE)),
        )
        return outcome, fake  # type: ignore[return-value]


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> Harness:
    box = Harness()

    monkeypatch.setattr(engine, "_monotonic", lambda: box.now)

    def fake_sleep(seconds: float) -> None:
        box.sleeps.append(seconds)
        box.now += seconds

    monkeypatch.setattr(engine, "_sleep", fake_sleep)
    return box


@pytest.fixture(autouse=True)
def restore_client():
    original = engine._client_for
    yield
    engine._client_for = original  # type: ignore[method-assign]


class TestHappyPath:
    def test_success_returns_validated_extraction(self, harness: Harness) -> None:
        outcome, fake = harness.run(http_result(success_body()))

        assert outcome.ok is True
        assert outcome.extraction is not None
        assert outcome.extraction.full_name == "Jane Doe"
        assert outcome.category is None
        assert outcome.usage.model == "acme/route-model"
        assert outcome.usage.route == "openrouter"
        assert fake.call_count == 1

    def test_attempt_receives_remaining_deadline_as_timeout(self, harness: Harness) -> None:
        outcome, fake = harness.run(http_result(success_body()))

        assert outcome.ok is True
        assert fake.calls[0][1] == pytest.approx(60.0)

    def test_payload_reaches_the_transport_unchanged(self, harness: Harness) -> None:
        outcome, fake = harness.run(http_result(success_body()))

        assert outcome.ok is True
        assert fake.calls[0][0] == {"model": "acme/profile-model"}


class TestRetryClassification:
    def test_transport_failure_is_retried_then_succeeds(self, harness: Harness) -> None:
        outcome, fake = harness.run(transport.TransportNetworkError(), http_result(success_body()))

        assert outcome.ok is True
        assert fake.call_count == 2

    def test_transport_timeout_is_retryable(self, harness: Harness) -> None:
        outcome, fake = harness.run(transport.TransportTimeout(), http_result(success_body()))

        assert outcome.ok is True
        assert fake.call_count == 2

    @pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
    def test_retryable_statuses_are_retried_then_fail_safe(
        self, harness: Harness, status: int
    ) -> None:
        outcome, fake = harness.run(
            http_result(b"{}", status=status), http_result(b"{}", status=status)
        )

        assert outcome.ok is False
        assert fake.call_count == 2
        assert outcome.category == (RATE_LIMITED if status == 429 else UNAVAILABLE)

    @pytest.mark.parametrize("status", [400, 401, 402, 403, 413, 422])
    def test_operator_statuses_fail_on_first_attempt(self, harness: Harness, status: int) -> None:
        outcome, fake = harness.run(http_result(b"{}", status=status), http_result(success_body()))

        assert outcome.ok is False
        assert outcome.category == CONFIGURATION_ERROR
        assert fake.call_count == 1

    def test_at_most_two_attempts_even_when_everything_is_retryable(self, harness: Harness) -> None:
        outcome, fake = harness.run(
            transport.TransportNetworkError(),
            transport.TransportNetworkError(),
            transport.TransportNetworkError(),
        )

        assert outcome.ok is False
        assert outcome.category == UNAVAILABLE
        assert fake.call_count == 2

    def test_schema_invalid_output_is_retried_then_fails_safe(self, harness: Harness) -> None:
        invalid = json.dumps(profile_payload(skills="nope"))
        outcome, fake = harness.run(
            http_result(completion_body(content=invalid)),
            http_result(completion_body(content=invalid)),
        )

        assert outcome.ok is False
        assert outcome.category == INVALID_RESPONSE
        assert fake.call_count == 2

    def test_malformed_json_is_not_retried(self, harness: Harness) -> None:
        outcome, fake = harness.run(
            http_result(completion_body(content="not json")), http_result(success_body())
        )

        assert outcome.ok is False
        assert outcome.category == INVALID_RESPONSE
        assert fake.call_count == 1

    def test_unverified_finish_reason_is_not_retried(self, harness: Harness) -> None:
        outcome, fake = harness.run(
            http_result(completion_body(finish_reason="content_filter")),
            http_result(success_body()),
        )

        assert outcome.ok is False
        assert outcome.category == INVALID_RESPONSE
        assert fake.call_count == 1

    def test_privacy_failure_is_never_retried_or_relaxed(self, harness: Harness) -> None:
        body = completion_body(error={"code": "privacy_restricted", "message": "x"})
        outcome, fake = harness.run(http_result(body), http_result(success_body()))

        assert outcome.ok is False
        assert outcome.category == PRIVACY_UNAVAILABLE
        assert fake.call_count == 1

    def test_unexpected_transport_exception_fails_safe(self, harness: Harness) -> None:
        outcome, fake = harness.run(ValueError("raw provider detail"), http_result(success_body()))

        assert outcome.ok is False
        assert outcome.category == INTERNAL_ERROR
        assert fake.call_count == 1

    def test_validator_crash_fails_safe(self, harness: Harness) -> None:
        def broken(_decoded: Any) -> Any:
            raise TypeError("boom")

        outcome, fake = harness.run(http_result(success_body()), validator=broken)

        assert outcome.ok is False
        assert outcome.category == INTERNAL_ERROR
        assert fake.call_count == 1


class TestDeadlineAndRetryAfter:
    def test_retry_after_leaving_remaining_time_executes_the_second_attempt(
        self, harness: Harness
    ) -> None:
        outcome, fake = harness.run(
            http_result(b"{}", status=429, headers={"Retry-After": "30"}),
            http_result(success_body()),
        )

        assert outcome.ok is True
        assert fake.call_count == 2
        assert harness.sleeps == [pytest.approx(30.0)]
        assert harness.now < 60.0

    def test_retry_after_beyond_the_deadline_fails_as_rate_limited(self, harness: Harness) -> None:
        outcome, fake = harness.run(
            http_result(b"{}", status=429, headers={"Retry-After": "600"}),
            http_result(success_body()),
        )

        # A bounded sleep could not produce another attempt within the
        # deadline, so the safe rate-limited category is returned at once.
        assert outcome.ok is False
        assert outcome.category == RATE_LIMITED
        assert fake.call_count == 1
        assert harness.sleeps == []

    def test_repeated_rate_limits_exhaust_attempts_as_rate_limited(self, harness: Harness) -> None:
        outcome, fake = harness.run(
            http_result(b"{}", status=429, headers={"Retry-After": "30"}),
            http_result(b"{}", status=429, headers={"Retry-After": "30"}),
        )

        assert outcome.ok is False
        assert outcome.category == RATE_LIMITED
        assert fake.call_count == 2
        assert len(harness.sleeps) == 1

    def test_retry_without_retry_after_uses_a_bounded_delay(self, harness: Harness) -> None:
        outcome, fake = harness.run(http_result(b"{}", status=503), http_result(success_body()))

        assert outcome.ok is True
        assert len(harness.sleeps) == 1
        assert 0 < harness.sleeps[0] <= 60.0

    def test_deadline_expires_during_a_failing_first_attempt(self, harness: Harness) -> None:
        class SlowFailure(FakeTransport):
            def post_json(self, payload: Any, *, timeout: float) -> transport.TransportResult:
                harness.advance(61)
                return super().post_json(payload, timeout=timeout)

        outcome, fake = harness.run(client=SlowFailure(transport.TransportNetworkError()))

        # The retry decision finds no remaining time and reports a timeout.
        assert outcome.ok is False
        assert outcome.category == TIMEOUT
        assert fake.call_count == 1
        assert harness.sleeps == []

    def test_slow_first_attempt_leaves_only_the_remaining_time(self, harness: Harness) -> None:
        class SlowSuccess(FakeTransport):
            def post_json(self, payload: Any, *, timeout: float) -> transport.TransportResult:
                harness.advance(10)
                return super().post_json(payload, timeout=timeout)

        outcome, fake = harness.run(
            client=SlowSuccess(
                transport.TransportNetworkError(),
                http_result(success_body()),
            )
        )

        assert outcome.ok is True
        assert fake.calls[1][1] == pytest.approx(49.0)
        assert fake.call_count == 2

    def test_deadline_failure_keeps_aggregated_usage(self, harness: Harness) -> None:
        invalid = json.dumps(profile_payload(skills="nope"))

        class SlowInvalid(FakeTransport):
            def post_json(self, payload: Any, *, timeout: float) -> transport.TransportResult:
                harness.advance(59.5)
                return super().post_json(payload, timeout=timeout)

        outcome, _fake = harness.run(
            client=SlowInvalid(
                http_result(completion_body(content=invalid)),
                http_result(b"{}", status=503),
            )
        )

        # The retry delay is clamped to the remaining time; the deadline then
        # expires with the first attempt's usage still aggregated.
        assert outcome.ok is False
        assert outcome.category == TIMEOUT
        assert outcome.usage.prompt_tokens == 100
        assert outcome.usage.completion_tokens == 50
        assert outcome.usage.cost == decimal.Decimal("0.020000")


class TestUsageAggregation:
    def test_usage_aggregates_across_attempts(self, harness: Harness) -> None:
        invalid = json.dumps(profile_payload(skills="nope"))
        outcome, _fake = harness.run(
            http_result(
                completion_body(
                    content=invalid,
                    model="acme/route-a",
                    prompt_tokens=100,
                    completion_tokens=40,
                    cost=0.01,
                )
            ),
            http_result(
                success_body(
                    model="acme/route-b", prompt_tokens=120, completion_tokens=60, cost=0.03
                )
            ),
        )

        assert outcome.ok is True
        assert outcome.usage.model == "acme/route-b"
        assert outcome.usage.prompt_tokens == 220
        assert outcome.usage.completion_tokens == 100
        assert outcome.usage.cost == decimal.Decimal("0.040000")

    def test_failed_attempts_add_no_usage(self, harness: Harness) -> None:
        outcome, _fake = harness.run(
            http_result(b"{}", status=503),
            http_result(b"{}", status=503),
        )

        assert outcome.ok is False
        assert outcome.usage == Usage.zero()

    def test_missing_cost_counts_as_zero(self, harness: Harness) -> None:
        outcome, _fake = harness.run(
            http_result(success_body(cost=None)),
        )

        assert outcome.ok is True
        assert outcome.usage.cost == decimal.Decimal("0")
