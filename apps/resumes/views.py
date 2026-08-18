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
from apps.resumes.services import (
    build_resume_default_draft,
    build_resume_document,
    open_resume,
    save_resume_draft,
)


def _form_snapshot(draft_forms: ResumeDraftForms) -> str:
    values: list[str] = []
    for formset in (
        draft_forms.header,
        draft_forms.sections,
        draft_forms.experiences,
        draft_forms.highlights,
        draft_forms.projects,
        draft_forms.educations,
        draft_forms.languages,
        draft_forms.skills,
    ):
        forms = formset.forms if hasattr(formset, "forms") else [formset]
        for form in forms:
            for field in form:
                value = form.initial.get(field.name, "")
                if isinstance(value, bool):
                    value = "1" if value else "0"
                values.append(f"{field.html_name}={value or ''}")
    return "&".join(sorted(values))


def _render_resume(
    request: HttpRequest,
    account: Account,
    resume: Resume,
    draft_forms: ResumeDraftForms,
    default_draft: dict[str, object] | None = None,
    reset_pending: bool = False,
    save_failed: bool = False,
    reset_scope: str | None = None,
) -> HttpResponse:
    resume.refresh_from_db()
    baseline_forms = build_resume_forms(resume=resume)
    if default_draft is None:
        default_draft = build_resume_default_draft(
            account=account,
            application_id=resume.application_id,
        )
    document = build_resume_document(account=account, resume=resume)
    return render(
        request,
        "resumes/detail.html",
        {
            "application": resume.application,
            "document": document,
            "draft_forms": draft_forms,
            "default_draft": default_draft,
            "reset_pending": reset_pending,
            "save_failed": save_failed,
            "reset_scope": reset_scope,
            "baseline_snapshot": _form_snapshot(baseline_forms),
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

    default_draft = build_resume_default_draft(
        account=account,
        application_id=resume.application_id,
    )
    if request.GET.get("reset") == "confirm":
        return render(
            request,
            "resumes/reset_confirm.html",
            {"application": resume.application},
        )
    reset_pending = request.GET.get("reset") == "confirmed"
    return _render_resume(
        request,
        account,
        resume,
        draft_forms=build_resume_forms(
            resume=resume,
            default_draft=default_draft if reset_pending else None,
        ),
        default_draft=default_draft,
        reset_pending=reset_pending,
    )


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
                rebuild_scope=request.POST.get("reset_scope") or None,
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
    return _render_resume(
        request,
        account,
        resume,
        draft_forms,
        reset_pending=request.POST.get("reset_resume_draft") == "1",
        save_failed=True,
        reset_scope=request.POST.get("reset_scope") or None,
    )
