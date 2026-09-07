"""Fixed, content-safe failure categories for the AI boundary.

Raw provider errors, prompts, source text, and raw outputs never travel with
an AIError: the fixed category is the only information that crosses the AI
boundary or reaches a candidate. Callers must switch on the category, never
on exception prose.
"""

__all__ = [
    "CONFIGURATION_ERROR",
    "CONSENT_REQUIRED",
    "INTERNAL_ERROR",
    "INVALID_INPUT",
    "INVALID_RESPONSE",
    "PRIVACY_UNAVAILABLE",
    "RATE_LIMITED",
    "SAFE_CATEGORIES",
    "TIMEOUT",
    "UNAVAILABLE",
    "AIError",
]

CONSENT_REQUIRED = "consent_required"
INVALID_INPUT = "invalid_input"
UNAVAILABLE = "unavailable"
RATE_LIMITED = "rate_limited"
TIMEOUT = "timeout"
INVALID_RESPONSE = "invalid_response"
PRIVACY_UNAVAILABLE = "privacy_unavailable"
CONFIGURATION_ERROR = "configuration_error"
INTERNAL_ERROR = "internal_error"

SAFE_CATEGORIES = frozenset(
    {
        CONSENT_REQUIRED,
        INVALID_INPUT,
        UNAVAILABLE,
        RATE_LIMITED,
        TIMEOUT,
        INVALID_RESPONSE,
        PRIVACY_UNAVAILABLE,
        CONFIGURATION_ERROR,
        INTERNAL_ERROR,
    }
)


class AIError(Exception):
    """A safe AI failure; only the fixed category crosses the boundary."""

    def __init__(self, category: str) -> None:
        if category not in SAFE_CATEGORIES:
            raise ValueError("AIError requires a fixed safe category")
        super().__init__(category)
        self.category = category
