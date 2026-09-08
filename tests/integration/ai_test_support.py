from apps.ai.conf import FEATURE_CANDIDATE_PROFILE, FEATURE_JOB_POSTING
from apps.ai.models import AIOperationSwitch


def ai_overrides(**overrides: object) -> dict[str, object]:
    configured: dict[str, object] = {
        "API_KEY": "test-key",
        "PROFILE_MODEL": "acme/profile-model",
        "POSTING_MODEL": "acme/posting-model",
    }
    configured.update(overrides)
    return configured


def enable_switches() -> None:
    for scope in ("global", FEATURE_CANDIDATE_PROFILE, FEATURE_JOB_POSTING):
        AIOperationSwitch.objects.create(scope=scope, enabled=True)
