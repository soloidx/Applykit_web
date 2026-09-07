"""Typed access to the AI consent-policy configuration.

Callers cannot override the configured policy; values come only from Django
settings, which operators control.
"""

from django.conf import settings

__all__ = ["DEFAULT_CONSENT_POLICY", "current_policy"]

DEFAULT_CONSENT_POLICY = "2026-09-initial-ai-imports"


def current_policy() -> str:
    configured = getattr(settings, "AI_CONSENT", {})
    policy = str(configured.get("POLICY", DEFAULT_CONSENT_POLICY)).strip()
    return policy or DEFAULT_CONSENT_POLICY
