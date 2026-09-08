"""Content-free operational and security telemetry for AI operations.

Every field must be a fixed identifier or a bounded count: the Account
identifier, the feature name, a typed outcome category, attempt counts,
durations, aggregate token and cost figures, and the telemetry version.
Source text, prompts, raw provider outputs, extracted values, and raw
exceptions never enter telemetry.
"""

import decimal
import logging
from collections.abc import Callable

from apps.ai.conf import FEATURE_CANDIDATE_PROFILE, FEATURE_JOB_POSTING
from apps.ai.errors import SAFE_CATEGORIES

__all__ = ["AI_TELEMETRY_VERSION", "log_event", "security_event"]

OPERATIONAL_LOGGER_NAME = "applykit.ai"
SECURITY_LOGGER_NAME = "applykit.security"

AI_TELEMETRY_VERSION = 1

_APPROVED_EVENTS = {
    "ai_operation_admitted": {"account_id", "feature", "version"},
    "ai_operation_rejected": {"account_id", "feature", "category", "version"},
    "ai_operation_completed": {
        "account_id",
        "feature",
        "outcome",
        "attempts",
        "duration_ms",
        "prompt_tokens",
        "completion_tokens",
        "cost",
        "version",
    },
    "ai_reservation_release_failed": {"account_id", "feature", "version"},
}
_APPROVED_FEATURES = {FEATURE_CANDIDATE_PROFILE, FEATURE_JOB_POSTING}
_APPROVED_OUTCOMES = SAFE_CATEGORIES | {"success"}
_MAX_ATTEMPTS = 2
_MAX_DURATION_MS = 120_000
_MAX_RESOURCE_COUNT = 1_000_000
_MAX_COST = decimal.Decimal("1000000")


def log_event(event: str, **fields: object) -> None:
    _write(logging.getLogger(OPERATIONAL_LOGGER_NAME).info, event, fields)


def security_event(event: str, **fields: object) -> None:
    _write(logging.getLogger(SECURITY_LOGGER_NAME).warning, event, fields)


def _write(writer: Callable[..., object], event: str, fields: dict[str, object]) -> None:
    allowed = _APPROVED_EVENTS.get(event)
    if allowed is None or set(fields) != allowed or not _valid_fields(event, fields):
        return
    try:
        writer(event, extra={"event": event, **fields})
    except Exception:
        # Telemetry must never break or leak through an AI operation.
        return


def _valid_fields(event: str, fields: dict[str, object]) -> bool:
    account_id = fields.get("account_id")
    if account_id is not None and (
        not isinstance(account_id, int) or isinstance(account_id, bool) or account_id < 0
    ):
        return False
    if fields.get("feature") not in _APPROVED_FEATURES:
        return False
    if fields.get("version") != AI_TELEMETRY_VERSION:
        return False
    if event == "ai_operation_rejected":
        return fields.get("category") in SAFE_CATEGORIES
    if event == "ai_operation_completed":
        if fields.get("outcome") not in _APPROVED_OUTCOMES:
            return False
        for name in ("attempts", "duration_ms", "prompt_tokens", "completion_tokens"):
            value = fields.get(name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                return False
            maximum = (
                _MAX_ATTEMPTS
                if name == "attempts"
                else (_MAX_DURATION_MS if name == "duration_ms" else _MAX_RESOURCE_COUNT)
            )
            if value > maximum:
                return False
        try:
            cost = decimal.Decimal(str(fields.get("cost")))
        except decimal.InvalidOperation, ValueError:
            return False
        return cost.is_finite() and 0 <= cost <= _MAX_COST
    return True
