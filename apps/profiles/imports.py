"""Candidate Profile import workflow services.

The import workflow is the only place a Candidate Profile is created
atomically from a reviewed browser-only draft. Nothing here persists before the
explicit save, and a version mismatch or a concurrently created profile is a
stale save that never force-replaces existing data.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import Account
from apps.ai import errors as ai_errors
from apps.ai.schemas import CandidateProfileExtraction
from apps.documents import protocol as document_protocol
from apps.profiles.models import CandidateProfile
from apps.profiles.versioning import InvalidProfileVersion, read_profile_version

__all__ = [
    "AI_FAILURE_MESSAGES",
    "CORE_FACT_FIELDS",
    "DOCUMENT_FAILURE_MESSAGES",
    "InvalidProfileContents",
    "PROFILE_FIELDS",
    "StaleProfileSave",
    "create_initial_profile",
    "failure_message",
    "has_core_facts",
]

PROFILE_FIELDS = (
    "contact_email",
    "full_name",
    "timezone",
    "professional_title",
    "professional_summary",
    "phone_number",
    "location",
    "linkedin_url",
    "portfolio_url",
)

# The core facts an onboarding import can propose. Nested Experience,
# Education, Project, Skill, and Language records are handled by later import
# slices and never make a core-only draft usable on their own.
CORE_FACT_FIELDS = (
    "full_name",
    "professional_title",
    "professional_summary",
    "phone_number",
    "location",
    "contact_email",
)

AI_FAILURE_MESSAGES = {
    ai_errors.CONSENT_REQUIRED: "AI import consent is required before a document can be read.",
    ai_errors.INVALID_INPUT: "The document did not contain enough readable text to import.",
    ai_errors.UNAVAILABLE: "AI-assisted imports are not available right now.",
    ai_errors.RATE_LIMITED: "You have reached the AI import limit. Try again later.",
    ai_errors.TIMEOUT: "The AI import took too long. Nothing was saved.",
    ai_errors.INVALID_RESPONSE: "We could not read this source safely. Nothing was saved.",
    ai_errors.PRIVACY_UNAVAILABLE: "AI-assisted imports are not available right now.",
    ai_errors.CONFIGURATION_ERROR: "AI-assisted imports are not configured. Nothing was saved.",
    ai_errors.INTERNAL_ERROR: "Something went wrong during the import. Nothing was saved.",
}

DOCUMENT_FAILURE_MESSAGES = {
    document_protocol.UNSUPPORTED_FORMAT: (
        "That file is not a supported DOCX or text-based PDF document."
    ),
    document_protocol.MALFORMED_DOCUMENT: (
        "That file could not be read as a DOCX or text-based PDF document."
    ),
    document_protocol.OVER_BUDGET: "That document is too large or too long to process safely.",
    document_protocol.PROCESSING_TIMEOUT: "Reading that document took too long.",
    document_protocol.EXTRACTION_UNAVAILABLE: "Document processing is not available right now.",
    document_protocol.INTERNAL_ERROR: "Something went wrong while reading that document.",
}

_DEFAULT_MESSAGE = "We could not complete the import. Nothing was saved."


class StaleProfileSave(Exception):
    """The reviewed version no longer matches the stored Candidate Profile."""


class InvalidProfileContents(Exception):
    """The reviewed values do not form a valid Candidate Profile."""

    def __init__(self, error: ValidationError) -> None:
        super().__init__("invalid profile contents")
        self.error = error


def has_core_facts(extraction: CandidateProfileExtraction) -> bool:
    return any(getattr(extraction, field) for field in CORE_FACT_FIELDS)


def failure_message(category: str, *, kind: str) -> str:
    if kind == "ai":
        return AI_FAILURE_MESSAGES.get(category, _DEFAULT_MESSAGE)
    return DOCUMENT_FAILURE_MESSAGES.get(category, _DEFAULT_MESSAGE)


@transaction.atomic
def create_initial_profile(
    *,
    account: Account,
    values: dict[str, Any],
    token: str,
) -> CandidateProfile:
    """Create the initial Candidate Profile for one reviewed absent-state draft.

    The Account lock serializes concurrent saves. A profile that appeared since
    the draft was processed, or a token whose state no longer matches, is a
    stale save: no existing data is replaced and no force path exists.
    """

    try:
        version = read_profile_version(token, account)
    except InvalidProfileVersion as error:
        raise StaleProfileSave from error

    Account.objects.select_for_update().get(pk=account.pk)
    if CandidateProfile.objects.select_for_update().filter(account=account).exists():
        raise StaleProfileSave
    if not version.is_absent:
        raise StaleProfileSave

    profile = CandidateProfile(
        account=account,
        **{field: values.get(field, "") for field in PROFILE_FIELDS},
    )
    try:
        profile.full_clean(exclude=["account"])
    except ValidationError as error:
        raise InvalidProfileContents(error) from error
    profile.save()
    return profile
