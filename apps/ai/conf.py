"""Typed access to the AI import configuration.

Callers cannot override the configured values; they come only from Django
settings, which operators control. Configuration is validated when an enabled
operation is invoked, never at startup, so an incomplete AI configuration
cannot break unrelated startup paths or management commands.
"""

import decimal
from dataclasses import dataclass
from typing import Any

from django.conf import settings

__all__ = [
    "FEATURE_CANDIDATE_PROFILE",
    "FEATURE_JOB_POSTING",
    "DEFAULT_BASE_URL",
    "DEFAULT_DEADLINE_SECONDS",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MAX_INPUT_TOKENS",
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "DEFAULT_PRICE_CEILING",
    "AISettings",
    "FeatureConfig",
    "ConfigurationError",
    "current_policy",
    "current_settings",
    "feature_config",
]

FEATURE_CANDIDATE_PROFILE = "candidate_profile_extraction"
FEATURE_JOB_POSTING = "job_posting_extraction"

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_DEADLINE_SECONDS = 60.0
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_MAX_INPUT_TOKENS = 60_000
DEFAULT_MAX_OUTPUT_TOKENS = 8_000
DEFAULT_PRICE_CEILING = "0.50"

DEFAULT_CONSENT_POLICY = "2026-09-initial-ai-imports"


class ConfigurationError(Exception):
    """The AI feature cannot run safely with the current configuration."""

    def __init__(self, problem: str) -> None:
        super().__init__(problem)
        self.problem = problem


@dataclass(frozen=True)
class AISettings:
    base_url: str
    api_key: str
    deadline_seconds: float
    max_attempts: int
    max_input_tokens: int
    max_output_tokens: int
    price_ceiling: decimal.Decimal
    feature_models: dict[str, str]


@dataclass(frozen=True)
class FeatureConfig:
    """The validated configuration for one enabled AI feature."""

    feature: str
    model: str
    base_url: str
    api_key: str
    deadline_seconds: float
    max_attempts: int
    max_input_tokens: int
    max_output_tokens: int
    price_ceiling: decimal.Decimal


def current_settings() -> AISettings:
    configured: Any = getattr(settings, "AI_IMPORTS", None) or {}
    return AISettings(
        base_url=_text_setting(configured, "BASE_URL", DEFAULT_BASE_URL, "base_url"),
        api_key=_text_setting(configured, "API_KEY", "", "credential"),
        deadline_seconds=_number_setting(
            configured, "DEADLINE_SECONDS", DEFAULT_DEADLINE_SECONDS, "deadline"
        ),
        max_attempts=_int_setting(configured, "MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS, "attempts"),
        max_input_tokens=_int_setting(
            configured, "MAX_INPUT_TOKENS", DEFAULT_MAX_INPUT_TOKENS, "input_limit"
        ),
        max_output_tokens=_int_setting(
            configured, "MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS, "output_limit"
        ),
        price_ceiling=_decimal_setting(configured, "PRICE_CEILING", "price_ceiling"),
        feature_models={
            FEATURE_CANDIDATE_PROFILE: _text_setting(
                configured, "PROFILE_MODEL", "", "profile_model"
            ),
            FEATURE_JOB_POSTING: _text_setting(configured, "POSTING_MODEL", "", "posting_model"),
        },
    )


def feature_config(feature: str) -> FeatureConfig:
    """Validate and return the configuration for one feature.

    Raises ConfigurationError when the feature cannot run safely. The raised
    exception is module-internal and is converted to the safe
    ``configuration_error`` category before anything crosses the AI boundary.
    """

    configured = current_settings()
    if feature not in configured.feature_models:
        raise ConfigurationError(f"unknown feature {feature}")
    model = configured.feature_models[feature]
    problems: list[str] = []
    if not configured.api_key:
        problems.append("credential")
    if not model:
        problems.append("model")
    if not configured.base_url.startswith(("http://", "https://")):
        problems.append("base_url")
    if configured.deadline_seconds <= 0:
        problems.append("deadline")
    if configured.max_attempts < 1:
        problems.append("attempts")
    if configured.max_input_tokens <= 0:
        problems.append("input_limit")
    if configured.max_output_tokens <= 0:
        problems.append("output_limit")
    if configured.price_ceiling <= 0:
        problems.append("price_ceiling")
    if problems:
        raise ConfigurationError(", ".join(sorted(problems)))
    return FeatureConfig(
        feature=feature,
        model=model,
        base_url=configured.base_url.rstrip("/"),
        api_key=configured.api_key,
        deadline_seconds=configured.deadline_seconds,
        max_attempts=configured.max_attempts,
        max_input_tokens=configured.max_input_tokens,
        max_output_tokens=configured.max_output_tokens,
        price_ceiling=configured.price_ceiling,
    )


def current_policy() -> str:
    configured = getattr(settings, "AI_CONSENT", {})
    policy = str(configured.get("POLICY", DEFAULT_CONSENT_POLICY)).strip()
    return policy or DEFAULT_CONSENT_POLICY


def _text_setting(configured: Any, key: str, default: str, label: str) -> str:
    value = configured.get(key)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ConfigurationError(label)
    return value.strip()


def _number_setting(configured: Any, key: str, default: float, label: str) -> float:
    value = configured.get(key)
    if value is None:
        return default
    try:
        return float(value)
    except TypeError, ValueError:
        raise ConfigurationError(label) from None


def _int_setting(configured: Any, key: str, default: int, label: str) -> int:
    value = configured.get(key)
    if value is None:
        return default
    try:
        return int(value)
    except TypeError, ValueError:
        raise ConfigurationError(label) from None


def _decimal_setting(configured: Any, key: str, label: str) -> decimal.Decimal:
    value = configured.get(key)
    if value is None:
        return decimal.Decimal(DEFAULT_PRICE_CEILING)
    try:
        parsed = decimal.Decimal(str(value))
    except decimal.InvalidOperation:
        raise ConfigurationError(label) from None
    if not parsed.is_finite():
        raise ConfigurationError(label)
    return parsed
    try:
        return decimal.Decimal(str(value))
    except decimal.InvalidOperation:
        return decimal.Decimal(DEFAULT_PRICE_CEILING)
