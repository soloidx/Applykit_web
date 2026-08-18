from __future__ import annotations

from typing import cast

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.accounts.access import verified_account_required
from apps.accounts.models import Account
from apps.applications.models import JobApplication
from apps.cover_letters.forms import CoverLetterForm
from apps.cover_letters.models import CoverLetter
from apps.cover_letters.services import (
    build_cover_letter_starter_template,
    delete_cover_letter,
    save_cover_letter,
)
from apps.profiles.models import CandidateProfile


def _is_htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"


def _redirect_or_htmx_redirect(request: HttpRequest, destination: str) -> HttpResponse:
    if _is_htmx(request):
        response = HttpResponse(status=200)
        response["HX-Redirect"] = destination
        return response
    return redirect(destination)


def _application_for_account(account: Account, application_id: int) -> JobApplication:
    return get_object_or_404(
        JobApplication.objects.select_related("company"),
        pk=application_id,
        account=account,
    )


def _render_detail(
    request: HttpRequest,
    *,
    account: Account,
    application: JobApplication,
    form: CoverLetterForm | None = None,
    editor_open: bool = False,
    save_failed: bool = False,
    editor_dirty: bool = False,
    starter_template: str | None = None,
) -> HttpResponse:
    cover_letter = CoverLetter.objects.filter(application=application).first()
    if starter_template is None:
        profile = CandidateProfile.objects.get(account=account)
        starter_template = build_cover_letter_starter_template(
            application=application,
            profile=profile,
        )
    saved_body = cover_letter.body_html if cover_letter else ""
    if form is None:
        form = CoverLetterForm(initial={"body_html": saved_body})
    return render(
        request,
        "cover_letters/detail.html",
        {
            "application": application,
            "cover_letter": cover_letter,
            "cover_letter_form": form,
            "editor_open": editor_open or cover_letter is not None,
            "save_failed": save_failed,
            "editor_dirty": editor_dirty or save_failed,
            "starter_template": starter_template,
            "baseline_body": saved_body,
        },
    )


@login_required
@verified_account_required
def cover_letter_detail(request: HttpRequest, application_id: int) -> HttpResponse:
    if request.method != "GET":
        return HttpResponse(status=405)
    account = cast(Account, request.user)
    application = _application_for_account(account, application_id)
    cover_letter = CoverLetter.objects.filter(application=application).first()
    draft = request.GET.get("draft") if cover_letter is None else None
    if draft not in {"blank", "template"}:
        return _render_detail(request, account=account, application=application)

    profile = CandidateProfile.objects.get(account=account)
    starter_template = build_cover_letter_starter_template(application=application, profile=profile)
    initial_body = starter_template if draft == "template" else ""
    return _render_detail(
        request,
        account=account,
        application=application,
        form=CoverLetterForm(initial={"body_html": initial_body}),
        editor_open=True,
        editor_dirty=draft == "template",
        starter_template=starter_template,
    )


@login_required
@verified_account_required
def cover_letter_save(request: HttpRequest, application_id: int) -> HttpResponse:
    if request.method != "POST":
        return HttpResponse(status=405)
    account = cast(Account, request.user)
    application = _application_for_account(account, application_id)
    form = CoverLetterForm(request.POST)
    if form.is_valid():
        try:
            save_cover_letter(
                account=account,
                application_id=application.pk,
                body_html=str(form.cleaned_data["body_html"]),
            )
        except ValidationError as error:
            form.add_error("body_html", error)
        except Exception as error:
            form.add_error(None, f"Save failed: {error}")
        else:
            return _redirect_or_htmx_redirect(
                request,
                reverse("cover_letter_detail", args=[application.pk]),
            )
    return _render_detail(
        request,
        account=account,
        application=application,
        form=form,
        editor_open=True,
        save_failed=True,
        editor_dirty=True,
    )


@login_required
@verified_account_required
def cover_letter_delete(request: HttpRequest, application_id: int) -> HttpResponse:
    if request.method != "POST":
        return HttpResponse(status=405)
    account = cast(Account, request.user)
    application = _application_for_account(account, application_id)
    cover_letter = get_object_or_404(CoverLetter, application=application)
    if request.POST.get("cancel"):
        return _redirect_or_htmx_redirect(
            request,
            reverse("cover_letter_detail", args=[application.pk]),
        )
    if not request.POST.get("confirm"):
        return render(
            request,
            "cover_letters/delete.html",
            {
                "application": application,
                "cover_letter": cover_letter,
                "dirty": request.POST.get("dirty") == "1",
            },
        )
    delete_cover_letter(account=account, application_id=application.pk)
    return _redirect_or_htmx_redirect(
        request,
        reverse("cover_letter_detail", args=[application.pk]),
    )
