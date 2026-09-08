import logging

import pytest

from apps.ai import telemetry
from apps.ai.conf import FEATURE_CANDIDATE_PROFILE

pytestmark = pytest.mark.unit


def read_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name.startswith("applykit")]


def test_operational_events_use_the_ai_logger(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        telemetry.log_event(
            "ai_operation_completed",
            account_id=7,
            feature=FEATURE_CANDIDATE_PROFILE,
            outcome="success",
            attempts=1,
            duration_ms=1250,
            prompt_tokens=10,
            completion_tokens=5,
            cost="0.02",
            version=telemetry.AI_TELEMETRY_VERSION,
        )

    records = read_records(caplog)
    assert len(records) == 1
    record = records[0]
    assert record.name == "applykit.ai"
    assert record.levelno == logging.INFO
    assert record.event == "ai_operation_completed"
    assert record.account_id == 7
    assert record.feature == FEATURE_CANDIDATE_PROFILE
    assert record.outcome == "success"
    assert record.attempts == 1
    assert record.duration_ms == 1250
    assert record.version == telemetry.AI_TELEMETRY_VERSION == 1


def test_security_events_use_the_security_logger_at_warning_level(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        telemetry.security_event(
            "ai_operation_rejected",
            account_id=7,
            feature=FEATURE_CANDIDATE_PROFILE,
            category="rate_limited",
            version=telemetry.AI_TELEMETRY_VERSION,
        )

    records = read_records(caplog)
    assert len(records) == 1
    record = records[0]
    assert record.name == "applykit.security"
    assert record.levelno == logging.WARNING
    assert record.event == "ai_operation_rejected"
    assert record.category == "rate_limited"


def test_telemetry_version_constant_is_stable() -> None:
    assert telemetry.AI_TELEMETRY_VERSION == 1


def test_unapproved_telemetry_fields_are_not_emitted(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        telemetry.log_event(
            "ai_operation_completed",
            account_id=7,
            feature=FEATURE_CANDIDATE_PROFILE,
            outcome="success",
            attempts=1,
            duration_ms=1,
            prompt_tokens=1,
            completion_tokens=1,
            cost="0.00",
            source_text="must not be logged",
            version=telemetry.AI_TELEMETRY_VERSION,
        )

    assert "must not be logged" not in caplog.text
    assert not read_records(caplog)


def test_unbounded_telemetry_counts_are_not_emitted(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        telemetry.log_event(
            "ai_operation_completed",
            account_id=7,
            feature=FEATURE_CANDIDATE_PROFILE,
            outcome="success",
            attempts=3,
            duration_ms=1,
            prompt_tokens=1,
            completion_tokens=1,
            cost="0.00",
            version=telemetry.AI_TELEMETRY_VERSION,
        )

    assert not read_records(caplog)
