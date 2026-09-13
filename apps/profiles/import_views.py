"""Candidate Profile import views.

The Source step uploads one DOCX or text-based PDF, the Process step extracts
and proposes core facts through the AI boundary, and the Review step renders a
browser-only, no-store draft. Nothing persists until an explicit save creates
the initial Candidate Profile atomically. Authentication, consent, admission,
and a header-based CSRF check all run before the multipart body is read.
"""

from __future__ import annotations

from typing import BinaryIO, cast
from urllib.parse import quote

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from apps.accounts.access import verified_account_required
from apps.accounts.models import Account
from apps.ai import errors as ai_errors
from apps.ai.operations import (
    assert_candidate_profile_import_preconditions,
    extract_candidate_profile,
)
from apps.documents.extraction import DocumentExtractionError, extract_document
from apps.profiles.csrf import header_csrf_protect
from apps.profiles.import_forms import ProfileImportReviewForm
from apps.profiles.imports import (
    InvalidProfileContents,
    StaleProfileSave,
    create_initial_profile,
    failure_message,
    has_core_facts,
)
from apps.profiles.models import CandidateProfile
from apps.profiles.upload_handlers import ProfileSourceUploadHandler
from apps.profiles.versioning import profile_version_token


def _no_store(response: HttpResponse) -> HttpResponse:
    response["Cache-Control"] = "no-store"
    return response


# Core facts the AI may propose. A blank one is an omission, shown as such.
_AI_PROPOSED_FIELDS = (
    "full_name",
    "contact_email",
    "professional_title",
    "professional_summary",
    "phone_number",
    "location",
)


def _review(
    request: HttpRequest,
    form: ProfileImportReviewForm,
    *,
    status: int = 200,
    stale: bool = False,
) -> HttpResponse:
    return _no_store(
        render(
            request,
            "profiles/import_review.html",
            {
                "form": form,
                "stage": "review",
                "stale": stale,
                "ai_fields": _AI_PROPOSED_FIELDS,
            },
            status=status,
        )
    )


def _failure(request: HttpRequest, message: str) -> HttpResponse:
    return _no_store(
        render(
            request,
            "profiles/import_failure.html",
            {"message": message, "stage": "failure"},
        )
    )


def _consent_redirect(request: HttpRequest) -> HttpResponse:
    target = reverse("profile")
    return redirect(f"{reverse('ai_consent')}?next={quote(target)}")


@header_csrf_protect
@login_required
@verified_account_required
def profile_import_process(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return HttpResponse(status=405)
    account = cast(Account, request.user)
    if CandidateProfile.objects.filter(account=account).exists():
        return redirect("profile")

    try:
        assert_candidate_profile_import_preconditions(account)
    except ai_errors.AIError as error:
        if error.category == ai_errors.CONSENT_REQUIRED:
            return _consent_redirect(request)
        return _failure(request, failure_message(error.category, kind="ai"))

    # By this point the request is authenticated, consented, admissible, and
    # CSRF-checked, so it is safe to read the multipart body. The route-specific
    # handler keeps the source in bounded memory, never ordinary Django storage.
    request.upload_handlers = [ProfileSourceUploadHandler(request)]
    source = request.FILES.get("source")
    if source is None:
        return _failure(request, "Choose one DOCX or PDF document to import.")

    try:
        extracted = extract_document(cast(BinaryIO, source))
    except DocumentExtractionError as error:
        return _failure(request, failure_message(error.category, kind="document"))

    try:
        extraction = extract_candidate_profile(account, extracted.text)
    except ai_errors.AIError as error:
        return _failure(request, failure_message(error.category, kind="ai"))

    if not has_core_facts(extraction):
        return _failure(
            request,
            "The document did not contain any profile facts we could review. Nothing was saved.",
        )

    initial = {
        "full_name": extraction.full_name or "",
        "professional_title": extraction.professional_title or "",
        "professional_summary": extraction.professional_summary or "",
        "phone_number": extraction.phone_number or "",
        "location": extraction.location or "",
        "contact_email": extraction.contact_email or account.email,
        "linkedin_url": "",
        "portfolio_url": "",
        "version_token": profile_version_token(account, None),
    }
    return _review(request, ProfileImportReviewForm(initial=initial))


@login_required
@verified_account_required
def profile_import_save(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return HttpResponse(status=405)
    account = cast(Account, request.user)
    form = ProfileImportReviewForm(request.POST)
    if not form.is_valid():
        return _review(request, form)

    try:
        create_initial_profile(
            account=account,
            values=form.cleaned_data,
            token=cast(str, form.cleaned_data["version_token"]),
        )
    except StaleProfileSave:
        return _review(request, form, stale=True)
    except InvalidProfileContents:
        return _failure(request, "We could not save this profile. Nothing was changed.")
    return redirect("dashboard")
