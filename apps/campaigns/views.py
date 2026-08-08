from typing import cast
from zoneinfo import ZoneInfo

from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.accounts.access import verified_account_required
from apps.accounts.models import Account
from apps.applications.models import RecruitmentEvent
from apps.campaigns.forms import CampaignForm
from apps.campaigns.models import Campaign
from apps.campaigns.progress import campaign_progress
from apps.campaigns.services import activate_campaign
from apps.profiles.access import minimum_profile_complete
from apps.profiles.models import CandidateProfile


def _is_htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"


def _redirect_or_htmx_redirect(request: HttpRequest) -> HttpResponse:
    if _is_htmx(request):
        response = HttpResponse(status=200)
        response["HX-Redirect"] = reverse("dashboard")
        return response
    return redirect("dashboard")


def dashboard_context(account: Account, form: CampaignForm | None = None) -> dict[str, object]:
    active_campaign = Campaign.objects.filter(
        account=account, status=Campaign.Status.ACTIVE
    ).first()
    candidate_timezone_name = CandidateProfile.objects.get(account=account).timezone
    candidate_timezone = ZoneInfo(candidate_timezone_name)
    upcoming_events = list(
        RecruitmentEvent.objects.filter(
            application__account=account,
            status=RecruitmentEvent.Status.SCHEDULED,
            scheduled_at__gt=timezone.now(),
        )
        .select_related("application__company")
        .order_by("scheduled_at", "pk")
    )
    for event in upcoming_events:
        event.display_scheduled_at = timezone.localtime(
            event.scheduled_at,
            candidate_timezone,
        ).replace(tzinfo=None)
    return {
        "active_campaign": active_campaign,
        "campaign_progress": campaign_progress(account, active_campaign)
        if active_campaign
        else None,
        "campaign_form": form or CampaignForm(),
        "upcoming_events": upcoming_events,
        "candidate_timezone": candidate_timezone_name,
    }


@login_required
@verified_account_required
def campaign_create(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return HttpResponse(status=405)

    account = cast(Account, request.user)
    if not minimum_profile_complete(account):
        if _is_htmx(request):
            response = HttpResponse(status=200)
            response["HX-Redirect"] = reverse("profile")
            return response
        return redirect("profile")
    form = CampaignForm(request.POST)
    if form.is_valid():
        try:
            activate_campaign(
                account,
                form.cleaned_data["weekly_target"],
                form.cleaned_data["monthly_target"],
            )
        except IntegrityError:
            form.add_error(None, "You already have an active campaign.")
        else:
            return _redirect_or_htmx_redirect(request)
    return render(request, "core/dashboard.html", dashboard_context(account, form))


@login_required
@verified_account_required
def campaign_archive(request: HttpRequest, campaign_id: int) -> HttpResponse:
    if request.method != "POST":
        return HttpResponse(status=405)

    account = cast(Account, request.user)
    with transaction.atomic():
        campaign = get_object_or_404(
            Campaign.objects.select_for_update(),
            pk=campaign_id,
            account=account,
            status=Campaign.Status.ACTIVE,
        )
        campaign.status = Campaign.Status.ARCHIVED
        campaign.archived_at = timezone.now()
        campaign.save(update_fields=["status", "archived_at"])
    return _redirect_or_htmx_redirect(request)
