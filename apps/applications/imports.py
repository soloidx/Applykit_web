"""Job Application import workflow services.

The paste import is the only place a Job Application is created atomically from
a reviewed browser-only draft. Nothing here persists before the explicit save,
and a failure creates nothing. The reviewed Company is reused only through an
exact canonical-domain or domain-alias match; otherwise a provisional Company
is created inside the same transaction that creates the Draft Job Application.
A cryptographically random creation token makes repeated submission of one
review return the original result instead of an accidental duplicate.
"""

from __future__ import annotations

import secrets
from typing import Any

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.accounts.models import Account
from apps.ai import errors as ai_errors
from apps.ai.schemas import JobPostingExtraction
from apps.applications.models import Company, JobApplication
from apps.applications.provenance import normalized_posting_url
from apps.applications.services import create_or_reuse_company, find_company_by_domain
from apps.campaigns.models import Campaign

__all__ = [
    "AI_FAILURE_MESSAGES",
    "CampaignUnavailable",
    "InvalidApplicationContents",
    "company_match_for_website",
    "create_imported_application",
    "failure_message",
    "has_usable_posting_facts",
    "matching_applications",
    "new_creation_token",
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

_CREATION_TOKEN_BYTES = 32


class CampaignUnavailable(Exception):
    """No Active Campaign owns the reviewed draft, so it cannot be created."""


class InvalidApplicationContents(Exception):
    """The reviewed values do not form a valid Job Application."""

    def __init__(self, error: ValidationError) -> None:
        super().__init__("invalid application contents")
        self.error = error


def failure_message(category: str) -> str:
    return AI_FAILURE_MESSAGES.get(category, _DEFAULT_MESSAGE)


def new_creation_token() -> str:
    """Return a fresh, unguessable token for one rendered review draft."""

    return secrets.token_urlsafe(_CREATION_TOKEN_BYTES)


def has_usable_posting_facts(extraction: JobPostingExtraction) -> bool:
    """A posting is reviewable with a role title or a meaningful description."""

    return bool(extraction.role_title or extraction.job_description)


def company_match_for_website(website: str) -> Company | None:
    """Return the Company an exact domain match would reuse, if any."""

    value = website.strip() if isinstance(website, str) else ""
    if not value:
        return None
    try:
        return find_company_by_domain(value)
    except ValidationError:
        return None


def matching_applications(*, account: Account, posting_url: str) -> list[JobApplication]:
    """Return the Account's applications that share the normalized posting URL."""

    normalized = normalized_posting_url(posting_url)
    if not normalized:
        return []
    candidates = (
        JobApplication.objects.filter(account=account)
        .exclude(posting_url="")
        .select_related("company", "campaign")
        .order_by("-updated_at", "-pk")
    )
    return [
        application
        for application in candidates
        if normalized_posting_url(application.posting_url) == normalized
    ]


@transaction.atomic
def create_imported_application(
    *,
    account: Account,
    values: dict[str, Any],
    token: str,
) -> JobApplication:
    """Create one Draft Job Application atomically for the reviewed draft.

    Repeated submission of the same creation token returns the original
    application. The Active Campaign is locked and rechecked so an archived
    campaign cannot accept an import. The Company is confirmed or provisionally
    created in the same transaction. Any validation or persistence failure rolls
    back the whole transaction and creates nothing.
    """

    existing = _application_for_token(account, token)
    if existing is not None:
        return existing

    campaign = (
        Campaign.objects.select_for_update()
        .filter(account=account, status=Campaign.Status.ACTIVE)
        .first()
    )
    if campaign is None:
        raise CampaignUnavailable

    try:
        with transaction.atomic():
            company, _created = create_or_reuse_company(
                str(values.get("company_name", "")),
                str(values.get("company_website", "")).strip() or None,
            )
            application = JobApplication(
                account=account,
                campaign=campaign,
                company=company,
                role_title=str(values.get("role_title", "")),
                job_description=str(values.get("job_description", "")),
                posting_url=str(values.get("posting_url", "")),
                location=str(values.get("location", "")),
                compensation=str(values.get("compensation", "")),
                creation_token=token,
            )
            application.full_clean(exclude=["account", "campaign", "company"])
            application.save()
    except ValidationError as error:
        raise InvalidApplicationContents(error) from error
    except IntegrityError:
        # A simultaneous submit of the same review won the unique creation
        # token. Return its result instead of an accidental duplicate.
        existing = _application_for_token(account, token)
        if existing is not None:
            return existing
        raise
    return application


def _application_for_token(account: Account, token: str) -> JobApplication | None:
    if not token:
        return None
    return JobApplication.objects.filter(account=account, creation_token=token).first()
