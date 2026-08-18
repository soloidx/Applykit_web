from __future__ import annotations

from typing import cast

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from apps.accounts.access import verified_account_required
from apps.accounts.models import Account
from apps.applications.models import JobApplication
from apps.profiles.models import CandidateProfile
from apps.resumes.forms import ResumeDraftForm
from apps.resumes.models import Resume
from apps.resumes.services import build_resume_document, open_resume, save_resume_draft


def _render_resume(
    request: HttpRequest,
    account: Account,
    resume: Resume,
    form: ResumeDraftForm,
) -> HttpResponse:
    resume.refresh_from_db()
    document = build_resume_document(account=account, resume=resume)
    return render(
        request,
        "resumes/detail.html",
        {
            "application": resume.application,
            "document": document,
            "form": form,
        },
    )


def _header_form(resume: Resume) -> ResumeDraftForm:
    return ResumeDraftForm(
        initial={
            field: getattr(resume, f"{field}_override") or ""
            for field in ResumeDraftForm.base_fields
        },
        resume=resume,
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

    return _render_resume(request, account, resume, _header_form(resume))


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

    form = ResumeDraftForm(request.POST, resume=resume)
    if form.is_valid():
        try:
            save_resume_draft(
                account=account,
                application_id=application_id,
                values=form.cleaned_data,
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            destination = reverse("resume_detail", args=[application_id])
            if request.headers.get("HX-Request") == "true":
                response = HttpResponse(status=200)
                response["HX-Redirect"] = destination
                return response
            return redirect(destination)
    return _render_resume(request, account, resume, form)
