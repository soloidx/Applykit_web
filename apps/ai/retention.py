"""Retention for AI records.

An idempotent purge deletes AI audits older than twelve months. Account
deletion removes an Account's consent, audits, and reservations through
ordinary cascade deletion. Source content, prompts, and raw provider
outputs are never stored, so there is nothing else to purge.
"""

import calendar
from datetime import datetime

from django.utils import timezone

from apps.ai.models import AIOperationAudit

__all__ = ["RETENTION_MONTHS", "purge_expired"]

RETENTION_MONTHS = 12


def purge_expired(*, now: datetime | None = None) -> int:
    """Delete AI audits past the retention window.

    Returns the number of deleted records; running it again deletes nothing.
    """

    moment = now if now is not None else timezone.now()
    cutoff = _cutoff(moment)
    deleted = 0
    deleted += AIOperationAudit.objects.filter(created_at__lt=cutoff).delete()[0] or 0
    return deleted


def _cutoff(now: datetime) -> datetime:
    total_months = now.year * 12 + now.month - 1 - RETENTION_MONTHS
    year, month_index = divmod(total_months, 12)
    month = month_index + 1
    day = min(now.day, calendar.monthrange(year, month)[1])
    return now.replace(year=year, month=month, day=day)
