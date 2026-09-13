"""Job Application import workflow services.

The paste import is the only place a Job Application is created atomically from
a reviewed browser-only draft. Nothing here persists before the explicit save,
and a failure creates nothing. The reviewed Company must already exist in the
shared catalog; this workflow never creates or mutates Company identity.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import Account
from apps.ai import errors as ai_errors
from apps.ai.schemas import JobPostingExtraction
from apps.applications.models import Company, JobApplication
from apps.campaigns.models import Campaign

__all__ = [
    "AI_FAILURE_MESSAGES",
    "CampaignUnavailable",
    "InvalidApplicationContents",
    "failure_message",
    "has_usable_posting_facts",
    "create_imported_application",
]

AI_FAILURE_MESSAGES = {
    ai_errors.CONSENT_REQUIRED: "AI import consent is required before a posting can be read.",
    ai_errors.INVALID_INPUT: "The pasted posting did not contain enough readable text to import.",
    ai_errors.UNAVAILABLE: "AI-assisted imports are not available right now.",
    ai_errors.RATE_LIMITED: "You have reached the AI import limit. Try again later.",
    ai_errors.TIMEOUT: "The AI import took too long. Nothing was saved.",
    ai_errors.INVALID_RESPONSE: "We could not read this posting safely. Nothing was saved.",
    ai_errors.PRIVACY_UNAVAILABLE: "AI-assisted imports are not available right now.",
    ai_errors.CONFIGURATION_ERROR: "AI-assisted imports are not configured. Nothing was saved.",
    ai_errors.INTERNAL_ERROR: "Something went wrong during the import. Nothing was saved.",
}

_DEFAULT_MESSAGE = "We could not complete the import. Nothing was saved."


class CampaignUnavailable(Exception):
    """No Active Campaign owns the reviewed draft, so it cannot be created."""


class InvalidApplicationContents(Exception):
    """The reviewed values do not form a valid Job Application."""

    def __init__(self, error: ValidationError) -> None:
        super().__init__("invalid application contents")
        self.error = error


def failure_message(category: str) -> str:
    return AI_FAILURE_MESSAGES.get(category, _DEFAULT_MESSAGE)


def has_usable_posting_facts(extraction: JobPostingExtraction) -> bool:
    """A posting is reviewable with a role title or a meaningful description."""

    return bool(extraction.role_title or extraction.job_description)


@transaction.atomic
def create_imported_application(
    *,
    account: Account,
    values: dict[str, Any],
    company: Company,
) -> JobApplication:
    """Create one Draft Job Application atomically for the reviewed draft.

    The Active Campaign is locked and rechecked at save time so an archived
    campaign cannot accept an import. A validation failure or any persistence
    failure rolls back the whole transaction and creates nothing.
    """

    campaign = (
        Campaign.objects.select_for_update()
        .filter(account=account, status=Campaign.Status.ACTIVE)
        .first()
    )
    if campaign is None:
        raise CampaignUnavailable

    application = JobApplication(
        account=account,
        campaign=campaign,
        company=company,
        role_title=str(values.get("role_title", "")),
        job_description=str(values.get("job_description", "")),
        location=str(values.get("location", "")),
        compensation=str(values.get("compensation", "")),
    )
    try:
        application.full_clean(exclude=["account", "campaign", "company"])
    except ValidationError as error:
        raise InvalidApplicationContents(error) from error
    application.save()
    return application
