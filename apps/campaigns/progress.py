from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from apps.accounts.models import Account
from apps.applications.models import JobApplication
from apps.campaigns.models import Campaign


@dataclass(frozen=True)
class CampaignPeriods:
    week_start: datetime
    week_end: datetime
    month_start: datetime
    month_end: datetime


@dataclass(frozen=True)
class CampaignProgress:
    submitted_this_week: int
    submitted_this_month: int


def campaign_periods(now: datetime, timezone_name: str) -> CampaignPeriods:
    timezone = ZoneInfo(timezone_name)
    local_now = now.astimezone(timezone)
    week_start_date = local_now.date() - timedelta(days=local_now.weekday())
    month_start_date = local_now.date().replace(day=1)
    next_month_start_date = (
        month_start_date.replace(year=month_start_date.year + 1, month=1)
        if month_start_date.month == 12
        else month_start_date.replace(month=month_start_date.month + 1)
    )
    week_start = datetime.combine(week_start_date, time.min, timezone)
    return CampaignPeriods(
        week_start=week_start,
        week_end=week_start + timedelta(days=7),
        month_start=datetime.combine(month_start_date, time.min, timezone),
        month_end=datetime.combine(next_month_start_date, time.min, timezone),
    )


def campaign_progress(
    account: Account,
    campaign: Campaign,
    now: datetime | None = None,
) -> CampaignProgress:
    periods = campaign_periods(now or timezone.now(), campaign.timezone)
    applications = JobApplication.objects.filter(
        account=account,
        campaign=campaign,
        first_submitted_at__isnull=False,
    )
    return CampaignProgress(
        submitted_this_week=applications.filter(
            first_submitted_at__gte=periods.week_start,
            first_submitted_at__lt=periods.week_end,
        ).count(),
        submitted_this_month=applications.filter(
            first_submitted_at__gte=periods.month_start,
            first_submitted_at__lt=periods.month_end,
        ).count(),
    )
