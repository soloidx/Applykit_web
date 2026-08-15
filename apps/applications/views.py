from typing import cast
from zoneinfo import ZoneInfo

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.accounts.access import verified_account_required
from apps.accounts.models import Account
from apps.applications.forms import (
    ApplicationStageForm,
    JobApplicationCreateForm,
    JobApplicationEditForm,
    RecruitmentEventForm,
)
from apps.applications.models import Company, JobApplication, RecruitmentEvent
from apps.applications.services import (
    create_or_reuse_company,
    create_recruitment_event,
    delete_application,
    transition_application,
    update_recruitment_event,
)
from apps.campaigns.models import Campaign
from apps.profiles.models import CandidateProfile


def _is_htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"


def _redirect_or_htmx_redirect(request: HttpRequest, destination: str) -> HttpResponse:
    if _is_htmx(request):
        response = HttpResponse(status=200)
        response["HX-Redirect"] = destination
        return response
    return redirect(destination)


@login_required
@verified_account_required
def application_board(request: HttpRequest) -> HttpResponse:
    account = cast(Account, request.user)
    active_campaign = Campaign.objects.filter(
        account=account,
        status=Campaign.Status.ACTIVE,
    ).first()
    applications_by_stage: dict[str, list[JobApplication]] = {
        value: [] for value, _ in JobApplication.Stage.choices
    }
    if active_campaign is not None:
        applications = (
            JobApplication.objects.filter(
                account=account,
                campaign=active_campaign,
            )
            .select_related("company")
            .order_by("-updated_at", "-pk")
        )
        for application in applications:
            applications_by_stage[application.stage].append(application)
    stage_columns = [
        {
            "value": value,
            "label": label,
            "applications": applications_by_stage[value],
        }
        for value, label in JobApplication.Stage.choices
    ]
    return render(
        request,
        "applications/board.html",
        {"active_campaign": active_campaign, "stage_columns": stage_columns},
    )


@login_required
@verified_account_required
def application_create(request: HttpRequest) -> HttpResponse:
    account = cast(Account, request.user)
    campaign = get_object_or_404(Campaign, account=account, status=Campaign.Status.ACTIVE)
    if request.method == "GET":
        form = JobApplicationCreateForm()
        query = request.GET.get("q", "").strip()
        if query:
            company_field = cast(forms.ModelChoiceField, form.fields["company"])
            company_field.queryset = Company.objects.filter(
                Q(name__icontains=query)
                | Q(canonical_domain__icontains=query)
                | Q(domain_aliases__domain__icontains=query)
            ).order_by("name")
        return render(request, "applications/form.html", {"form": form, "company_query": query})
    if request.method != "POST":
        return HttpResponse(status=405)

    form = JobApplicationCreateForm(request.POST)
    if form.is_valid():
        company = form.cleaned_data["company"]
        reused = False
        if company is None:
            try:
                company, created = create_or_reuse_company(
                    str(form.cleaned_data["company_name"]),
                    str(form.cleaned_data["website"] or "") or None,
                )
            except ValidationError as error:
                form.add_error("website", error)
            else:
                reused = not created
        if company is not None and not form.errors:
            application = JobApplication.objects.create(
                account=account,
                campaign=campaign,
                company=company,
                role_title=str(form.cleaned_data["role_title"]),
                job_description=str(form.cleaned_data["job_description"]),
                posting_url=str(form.cleaned_data["posting_url"]),
                location=str(form.cleaned_data["location"]),
                compensation=str(form.cleaned_data["compensation"]),
                source=str(form.cleaned_data["source"]),
                private_notes=str(form.cleaned_data["private_notes"]),
            )
            if reused:
                messages.info(request, "An existing company matched that website and was reused.")
            return _redirect_or_htmx_redirect(
                request,
                reverse("application_edit", args=[application.pk]),
            )
    return render(request, "applications/form.html", {"form": form})


@login_required
@verified_account_required
def application_edit(request: HttpRequest, application_id: int) -> HttpResponse:
    account = cast(Account, request.user)
    application = get_object_or_404(
        JobApplication,
        pk=application_id,
        account=account,
        stage=JobApplication.Stage.DRAFT,
    )
    if request.method == "GET":
        return render(
            request,
            "applications/form.html",
            {"form": JobApplicationEditForm(instance=application), "application": application},
        )
    if request.method != "POST":
        return HttpResponse(status=405)

    form = JobApplicationEditForm(request.POST, instance=application)
    if form.is_valid():
        form.save()
        return _redirect_or_htmx_redirect(
            request,
            reverse("application_edit", args=[application.pk]),
        )
    return render(request, "applications/form.html", {"form": form, "application": application})


@login_required
@verified_account_required
def application_detail(request: HttpRequest, application_id: int) -> HttpResponse:
    account = cast(Account, request.user)
    application = get_object_or_404(
        JobApplication.objects.prefetch_related("stage_transitions"),
        pk=application_id,
        account=account,
    )
    stage_form = ApplicationStageForm(
        request.POST or None,
        initial={"stage": application.stage},
    )
    if request.method == "POST":
        if stage_form.is_valid():
            try:
                transition_application(
                    account=account,
                    application_id=application.pk,
                    stage=str(stage_form.cleaned_data["stage"]),
                )
            except ValidationError as error:
                stage_form.add_error("stage", error)
            else:
                if _is_htmx(request):
                    response = HttpResponse(status=200)
                    response["HX-Redirect"] = reverse("application_detail", args=[application.pk])
                    return response
                return redirect("application_detail", application_id=application.pk)
        else:
            return render(
                request,
                "applications/detail.html",
                _application_detail_context(account, application, stage_form=stage_form),
            )
    elif request.method != "GET":
        return HttpResponse(status=405)
    return render(
        request,
        "applications/detail.html",
        _application_detail_context(account, application, stage_form=stage_form),
    )


@login_required
@verified_account_required
def application_delete(request: HttpRequest, application_id: int) -> HttpResponse:
    account = cast(Account, request.user)
    application = get_object_or_404(
        JobApplication.objects.select_related("campaign", "company"),
        pk=application_id,
        account=account,
    )
    context = {
        "application": application,
        "contributes_to_progress": application.first_submitted_at is not None,
    }
    if request.method == "GET":
        return render(request, "applications/delete.html", context)
    if request.method != "POST":
        return HttpResponse(status=405)
    if request.POST.get("cancel"):
        return _redirect_or_htmx_redirect(
            request,
            reverse("application_detail", args=[application.pk]),
        )
    if request.POST.get("confirm"):
        delete_application(account=account, application_id=application.pk)
        return _redirect_or_htmx_redirect(request, reverse("dashboard"))
    return render(request, "applications/delete.html", context, status=400)


def _application_detail_context(
    account: Account,
    application: JobApplication,
    *,
    stage_form: ApplicationStageForm | None = None,
    event_form: RecruitmentEventForm | None = None,
    event_forms: dict[int, RecruitmentEventForm] | None = None,
) -> dict[str, object]:
    timezone_name = CandidateProfile.objects.get(account=account).timezone
    candidate_timezone = ZoneInfo(timezone_name)
    events = list(application.recruitment_events.all())
    for event in events:
        event.display_scheduled_at = timezone.localtime(
            event.scheduled_at,
            candidate_timezone,
        ).replace(tzinfo=None)
        if event_forms and event.pk in event_forms:
            event.form = event_forms[event.pk]
        elif event.status == RecruitmentEvent.Status.SCHEDULED and not hasattr(event, "form"):
            event.form = RecruitmentEventForm(
                timezone_name=timezone_name,
                instance=event,
            )
    return {
        "application": application,
        "stage_form": stage_form or ApplicationStageForm(initial={"stage": application.stage}),
        "event_form": event_form or RecruitmentEventForm(timezone_name=timezone_name),
        "recruitment_events": events,
        "candidate_timezone": timezone_name,
    }


@login_required
@verified_account_required
def recruitment_event_create(request: HttpRequest, application_id: int) -> HttpResponse:
    account = cast(Account, request.user)
    application = get_object_or_404(JobApplication, pk=application_id, account=account)
    if request.method != "POST":
        return HttpResponse(status=405)

    timezone_name = CandidateProfile.objects.get(account=account).timezone
    form = RecruitmentEventForm(request.POST, timezone_name=timezone_name)
    if form.is_valid():
        create_recruitment_event(
            account=account,
            application_id=application.pk,
            event_type=str(form.cleaned_data["event_type"]),
            custom_title=str(form.cleaned_data["custom_title"]),
            scheduled_at=form.cleaned_data["scheduled_at"],
        )
        return _redirect_or_htmx_redirect(
            request,
            reverse("application_detail", args=[application.pk]),
        )
    return render(
        request,
        "applications/detail.html",
        _application_detail_context(account, application, event_form=form),
    )


@login_required
@verified_account_required
def recruitment_event_edit(
    request: HttpRequest,
    application_id: int,
    event_id: int,
) -> HttpResponse:
    account = cast(Account, request.user)
    application = get_object_or_404(JobApplication, pk=application_id, account=account)
    event = get_object_or_404(
        RecruitmentEvent,
        pk=event_id,
        application=application,
        status=RecruitmentEvent.Status.SCHEDULED,
    )
    if request.method != "POST":
        return HttpResponse(status=405)

    form = RecruitmentEventForm(
        request.POST,
        instance=event,
        timezone_name=CandidateProfile.objects.get(account=account).timezone,
    )
    if form.is_valid():
        update_recruitment_event(
            account=account,
            application_id=application.pk,
            event_id=event.pk,
            event_type=str(form.cleaned_data["event_type"]),
            custom_title=str(form.cleaned_data["custom_title"]),
            scheduled_at=form.cleaned_data["scheduled_at"],
            status=str(form.cleaned_data["status"]),
        )
        return _redirect_or_htmx_redirect(
            request,
            reverse("application_detail", args=[application.pk]),
        )
    event.form = form
    return render(
        request,
        "applications/detail.html",
        _application_detail_context(account, application, event_forms={event.pk: form}),
    )
