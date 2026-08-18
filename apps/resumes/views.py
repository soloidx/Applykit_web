from __future__ import annotations

from typing import cast

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from apps.accounts.access import verified_account_required
from apps.accounts.models import Account
from apps.applications.models import JobApplication
from apps.profiles.models import CandidateProfile
from apps.resumes.forms import (
    ResumeDraftForms,
    build_resume_forms,
)
from apps.resumes.models import Resume
from apps.resumes.services import build_resume_document, open_resume, save_resume_draft


def _render_resume(
    request: HttpRequest,
    account: Account,
    resume: Resume,
    draft_forms: ResumeDraftForms,
) -> HttpResponse:
    resume.refresh_from_db()
    document = build_resume_document(account=account, resume=resume)
    return render(
        request,
        "resumes/detail.html",
        {
            "application": resume.application,
            "document": document,
            "draft_forms": draft_forms,
        },
    )


@login_required
@verified_account_required
def resume_detail(request: HttpRequest, application_id: int) -> HttpResponse:
    if request.method != "GET":
        return HttpResponse(status=405)
    account = cast(Account, request.user)
    try:
        resume, _created = open_resume(account=account, application_id=application_id)
    except JobApplication.DoesNotExist, CandidateProfile.DoesNotExist:
        return HttpResponse(status=404)

    return _render_resume(request, account, resume, draft_forms=build_resume_forms(resume=resume))


@login_required
@verified_account_required
def resume_save(request: HttpRequest, application_id: int) -> HttpResponse:
    if request.method != "POST":
        return HttpResponse(status=405)
    account = cast(Account, request.user)
    try:
        resume = Resume.objects.select_related("application").get(
            application__pk=application_id,
            application__account=account,
        )
    except Resume.DoesNotExist:
        return HttpResponse(status=404)

    draft_forms = build_resume_forms(resume=resume, data=request.POST)
    is_valid = draft_forms.is_valid()
    if is_valid:
        try:
            save_resume_draft(
                account=account,
                application_id=application_id,
                values=draft_forms.as_draft(),
            )
        except Exception as error:
            message = f"Save failed: {error}"
            draft_forms.header.add_error(None, message)
        else:
            destination = reverse("resume_detail", args=[application_id])
            if request.headers.get("HX-Request") == "true":
                response = HttpResponse(status=200)
                response["HX-Redirect"] = destination
                return response
            return redirect(destination)
    return _render_resume(request, account, resume, draft_forms)
