"""Job Application paste-import views.

The Source step offers pasting posting text for extraction alongside the
ordinary manual path and never retrieves a URL. The Process step normalizes the
pasted text locally, proposes only explicit posting facts through the AI
boundary, and renders a browser-only, no-store review draft. Nothing persists
until an explicit save creates one Draft Job Application atomically.
"""

from __future__ import annotations

from typing import cast
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from apps.accounts.access import verified_account_required
from apps.accounts.models import Account
from apps.ai import errors as ai_errors
from apps.ai.operations import assert_job_posting_import_preconditions, extract_job_posting
from apps.applications.import_forms import JobApplicationImportReviewForm
from apps.applications.imports import (
    CampaignUnavailable,
    InvalidApplicationContents,
    create_imported_application,
    failure_message,
    has_usable_posting_facts,
)
from apps.applications.models import Company
from apps.applications.posting_text import PostingTextRejected, normalize_posting_text
from apps.campaigns.models import Campaign

__all__ = ["application_import_process", "application_import_save"]

_EMPTY_POSTING_MESSAGE = "Paste the job posting text before importing."
_UNUSABLE_POSTING_MESSAGE = (
    "The posting did not contain a role title or description we could review. Nothing was saved."
)
_NO_CAMPAIGN_MESSAGE = "Activate a campaign before importing a job application."


def _no_store(response: HttpResponse) -> HttpResponse:
    response["Cache-Control"] = "no-store"
    return response


def _is_htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"


def _redirect_or_htmx_redirect(request: HttpRequest, destination: str) -> HttpResponse:
    if _is_htmx(request):
        response = HttpResponse(status=200)
        response["HX-Redirect"] = destination
        return response
    return redirect(destination)


def _review(
    request: HttpRequest,
    form: JobApplicationImportReviewForm,
    *,
    current: str = "review",
    status: int = 200,
) -> HttpResponse:
    return _no_store(
        render(
            request,
            "applications/import_review.html",
            {"form": form, "stage": "review", "current": current},
            status=status,
        )
    )


def _failure(request: HttpRequest, message: str) -> HttpResponse:
    return _no_store(
        render(
            request,
            "applications/import_failure.html",
            {"message": message, "stage": "failure"},
        )
    )


def _consent_redirect(request: HttpRequest) -> HttpResponse:
    target = reverse("application_create")
    destination = f"{reverse('ai_consent')}?next={quote(target)}"
    return _no_store(_redirect_or_htmx_redirect(request, destination))


@login_required
@verified_account_required
def application_import_process(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return HttpResponse(status=405)
    account = cast(Account, request.user)
    if not Campaign.objects.filter(account=account, status=Campaign.Status.ACTIVE).exists():
        return _failure(request, _NO_CAMPAIGN_MESSAGE)

    try:
        assert_job_posting_import_preconditions(account)
    except ai_errors.AIError as error:
        if error.category == ai_errors.CONSENT_REQUIRED:
            return _consent_redirect(request)
        return _failure(request, failure_message(error.category))

    try:
        posting_text = normalize_posting_text(request.POST.get("posting_text", ""))
    except PostingTextRejected:
        return _failure(request, _EMPTY_POSTING_MESSAGE)

    try:
        extraction = extract_job_posting(account, posting_text)
    except ai_errors.AIError as error:
        return _failure(request, failure_message(error.category))

    if not has_usable_posting_facts(extraction):
        return _failure(request, _UNUSABLE_POSTING_MESSAGE)

    form = JobApplicationImportReviewForm(
        initial={
            "role_title": extraction.role_title or "",
            "job_description": extraction.job_description or "",
            "location": extraction.location or "",
            "compensation": extraction.compensation or "",
        }
    )
    return _review(request, form)


@login_required
@verified_account_required
def application_import_save(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return HttpResponse(status=405)
    account = cast(Account, request.user)
    form = JobApplicationImportReviewForm(request.POST)
    if not form.is_valid():
        return _review(request, form, current="save")

    try:
        application = create_imported_application(
            account=account,
            values=form.cleaned_data,
            company=cast(Company, form.cleaned_data["company"]),
        )
    except CampaignUnavailable:
        return _failure(request, _NO_CAMPAIGN_MESSAGE)
    except InvalidApplicationContents:
        form.add_error(None, "We could not save this application. Nothing was created.")
        return _review(request, form, current="save")
    except Exception:
        form.add_error(None, "Something went wrong while saving. Nothing was created.")
        return _review(request, form, current="save")
    messages.success(request, "Draft application created from the reviewed posting.")
    return _redirect_or_htmx_redirect(
        request,
        reverse("application_detail", args=[application.pk]),
    )
