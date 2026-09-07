import decimal

import pytest
from django.test import override_settings

from apps.ai.conf import (
    DEFAULT_DEADLINE_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_INPUT_TOKENS,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_PRICE_CEILING,
    FEATURE_CANDIDATE_PROFILE,
    FEATURE_JOB_POSTING,
    AISettings,
    ConfigurationError,
    current_policy,
    current_settings,
    feature_config,
)
from apps.ai.errors import CONFIGURATION_ERROR, AIError

pytestmark = pytest.mark.unit


def enabled_settings(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "API_KEY": "test-key",
        "PROFILE_MODEL": "acme/profile-model",
        "POSTING_MODEL": "acme/posting-model",
    }
    base.update(overrides)
    return base


def test_defaults_are_safe_and_disabled() -> None:
    configured = current_settings()

    assert configured.base_url == "https://openrouter.ai/api/v1"
    assert configured.api_key == ""
    assert configured.deadline_seconds == DEFAULT_DEADLINE_SECONDS == 60.0
    assert configured.max_attempts == DEFAULT_MAX_ATTEMPTS == 2
    assert configured.max_input_tokens == DEFAULT_MAX_INPUT_TOKENS
    assert configured.max_output_tokens == DEFAULT_MAX_OUTPUT_TOKENS
    assert configured.price_ceiling == decimal.Decimal(DEFAULT_PRICE_CEILING)
    assert configured.feature_models == {
        FEATURE_CANDIDATE_PROFILE: "",
        FEATURE_JOB_POSTING: "",
    }


def test_defaults_do_not_break_startup_without_the_setting() -> None:
    with override_settings(AI_IMPORTS=None):
        assert isinstance(current_settings(), AISettings)
        with pytest.raises(ConfigurationError):
            feature_config(FEATURE_CANDIDATE_PROFILE)


@override_settings(AI_IMPORTS=enabled_settings())
def test_enabled_feature_configuration_is_typed() -> None:
    configured = feature_config(FEATURE_CANDIDATE_PROFILE)

    assert configured.model == "acme/profile-model"
    assert configured.api_key == "test-key"
    assert configured.deadline_seconds == 60.0
    assert configured.max_attempts == 2
    assert configured.price_ceiling == decimal.Decimal("0.50")


@override_settings(AI_IMPORTS=enabled_settings())
def test_each_feature_uses_its_own_model() -> None:
    assert feature_config(FEATURE_CANDIDATE_PROFILE).model == "acme/profile-model"
    assert feature_config(FEATURE_JOB_POSTING).model == "acme/posting-model"


@override_settings(AI_IMPORTS=enabled_settings(API_KEY=""))
def test_missing_credential_fails_closed() -> None:
    with pytest.raises(ConfigurationError):
        feature_config(FEATURE_CANDIDATE_PROFILE)


@override_settings(AI_IMPORTS=enabled_settings(PROFILE_MODEL=""))
def test_disabled_feature_fails_closed() -> None:
    with pytest.raises(ConfigurationError):
        feature_config(FEATURE_CANDIDATE_PROFILE)
    assert feature_config(FEATURE_JOB_POSTING).model == "acme/posting-model"


@override_settings(AI_IMPORTS=enabled_settings(DEADLINE_SECONDS=0))
def test_non_positive_deadline_is_rejected() -> None:
    with pytest.raises(ConfigurationError):
        feature_config(FEATURE_JOB_POSTING)


@override_settings(AI_IMPORTS=enabled_settings(MAX_ATTEMPTS=0))
def test_attempts_below_one_are_rejected() -> None:
    with pytest.raises(ConfigurationError):
        feature_config(FEATURE_JOB_POSTING)


@override_settings(AI_IMPORTS=enabled_settings(PRICE_CEILING="0"))
def test_non_positive_price_ceiling_is_rejected() -> None:
    with pytest.raises(ConfigurationError):
        feature_config(FEATURE_JOB_POSTING)


@override_settings(AI_IMPORTS=enabled_settings(BASE_URL="not-a-url"))
def test_invalid_base_url_is_rejected() -> None:
    with pytest.raises(ConfigurationError):
        feature_config(FEATURE_CANDIDATE_PROFILE)


def test_malformed_values_fail_closed() -> None:
    with override_settings(
        AI_IMPORTS={
            "API_KEY": "test-key",
            "PROFILE_MODEL": "acme/profile-model",
            "DEADLINE_SECONDS": "not-a-number",
        }
    ):
        with pytest.raises(ConfigurationError):
            feature_config(FEATURE_CANDIDATE_PROFILE)
    with override_settings(
        AI_IMPORTS={
            "API_KEY": "test-key",
            "POSTING_MODEL": "acme/posting-model",
            "MAX_ATTEMPTS": "zero",
        }
    ):
        with pytest.raises(ConfigurationError):
            feature_config(FEATURE_JOB_POSTING)
    with override_settings(
        AI_IMPORTS={
            "API_KEY": "test-key",
            "POSTING_MODEL": "acme/posting-model",
            "PRICE_CEILING": "bad",
        }
    ):
        with pytest.raises(ConfigurationError):
            feature_config(FEATURE_JOB_POSTING)


def test_consent_policy_identifier_comes_from_settings() -> None:
    with override_settings(AI_CONSENT={"POLICY": "test-policy-v1"}):
        assert current_policy() == "test-policy-v1"

    with override_settings(AI_CONSENT={"POLICY": "   "}):
        assert current_policy() == "2026-09-initial-ai-imports"


def test_ai_error_rejects_unknown_categories() -> None:
    with pytest.raises(ValueError):
        AIError("provider_said_something_raw")


def test_safe_error_category_passes_through() -> None:
    failure = AIError(CONFIGURATION_ERROR)

    assert failure.category == CONFIGURATION_ERROR
    assert str(failure) == CONFIGURATION_ERROR
