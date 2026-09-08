"""The admission gate for AI operations.

Every logical AI operation is admitted before dispatch. Admission rechecks
the operator-controlled switches, enforces at most one in-flight operation
per Account, bounds started operations per rolling window, and reserves the
per-request cost ceiling against a configurable rolling-30-day Account cost
ceiling. Rejected admission creates no provider request; unavailable budget
state fails closed.

Only fixed safe categories leave this module. A switch refusal is
``unavailable``; an in-flight conflict, an exhausted rolling window, or an
exhausted Account cost ceiling is ``rate_limited``; budget state that cannot
be read fails closed as ``internal_error``.
"""

import decimal
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.accounts.models import Account
from apps.ai import telemetry
from apps.ai.conf import FeatureConfig
from apps.ai.errors import INTERNAL_ERROR, RATE_LIMITED, UNAVAILABLE, AIError
from apps.ai.models import AIOperationAudit, AIOperationReservation, AIOperationSwitch

__all__ = ["Reservation", "admit", "reconcile", "switches_enabled"]

MAX_STARTED_15_MINUTES = 5
MAX_STARTED_24_HOURS = 20
BUDGET_WINDOW_DAYS = 30


@dataclass(frozen=True)
class Reservation:
    """A held admission slot for one logical AI operation."""

    id: int
    feature: str
    reserved_cost: decimal.Decimal


def switches_enabled(feature: str) -> bool:
    """Whether the global scope and the feature scope are both enabled."""

    rows = AIOperationSwitch.objects.filter(scope__in=[AIOperationSwitch.GLOBAL_SCOPE, feature])
    enabled = {row.scope: row.enabled for row in rows}
    return bool(enabled.get(AIOperationSwitch.GLOBAL_SCOPE)) and bool(enabled.get(feature))


def admit(account: Account, config: FeatureConfig) -> Reservation:
    """Admit one logical operation or raise the safe refusal category."""

    try:
        reservation = _admit(account, config)
    except AIError as rejection:
        telemetry.security_event(
            "ai_operation_rejected",
            account_id=account.pk,
            feature=config.feature,
            category=rejection.category,
            version=telemetry.AI_TELEMETRY_VERSION,
        )
        raise
    telemetry.log_event(
        "ai_operation_admitted",
        account_id=account.pk,
        feature=config.feature,
        version=telemetry.AI_TELEMETRY_VERSION,
    )
    return reservation


def reconcile(reservation: Reservation) -> None:
    """Release the in-flight hold after the operation's audit is recorded.

    The hold is released best-effort. A failed release errs toward
    over-counting spend rather than under-counting it.
    """

    try:
        with transaction.atomic():
            row = AIOperationReservation.objects.select_related("account").get(pk=reservation.id)
            Account.objects.select_for_update().get(pk=row.account_id)
            if row.status == AIOperationReservation.STATUS_IN_FLIGHT:
                row.status = AIOperationReservation.STATUS_COMPLETED
                row.save(update_fields=["status"])
    except Exception:
        telemetry.security_event(
            "ai_reservation_release_failed",
            account_id=None,
            feature=reservation.feature,
            version=telemetry.AI_TELEMETRY_VERSION,
        )


def _admit(account: Account, config: FeatureConfig) -> Reservation:
    now = timezone.now()
    try:
        with transaction.atomic():
            Account.objects.select_for_update().get(pk=account.pk)
            if not switches_enabled(config.feature):
                raise AIError(UNAVAILABLE)
            started_15m = _count_started(account, now - timedelta(minutes=15))
            started_24h = _count_started(account, now - timedelta(hours=24))
            spent = _budget_spent(account, now)

            if started_15m >= MAX_STARTED_15_MINUTES or started_24h >= MAX_STARTED_24_HOURS:
                raise AIError(RATE_LIMITED)
            if spent + config.price_ceiling > config.account_cost_ceiling:
                raise AIError(RATE_LIMITED)

            try:
                with transaction.atomic():
                    row = AIOperationReservation.objects.create(
                        account=account,
                        feature=config.feature,
                        reserved_cost=config.price_ceiling,
                    )
            except IntegrityError:
                # A concurrent admission for this Account took the single
                # in-flight slot before the Account lock was acquired.
                raise AIError(RATE_LIMITED) from None
    except AIError:
        raise
    except Exception:
        # Unavailable budget state fails closed.
        raise AIError(INTERNAL_ERROR) from None
    return Reservation(
        id=row.pk,
        feature=row.feature,
        reserved_cost=row.reserved_cost,
    )


def _count_started(account: Account, since: datetime) -> int:
    return AIOperationReservation.objects.filter(
        account=account,
        created_at__gte=since,
    ).count()


def _budget_spent(account: Account, now: datetime) -> decimal.Decimal:
    window_start = now - timedelta(days=BUDGET_WINDOW_DAYS)
    audit_total = AIOperationAudit.objects.filter(
        account=account,
        created_at__gte=window_start,
    ).aggregate(total=Sum("cost"))["total"] or decimal.Decimal("0")
    reserved_total = AIOperationReservation.objects.filter(
        account=account,
        status=AIOperationReservation.STATUS_IN_FLIGHT,
    ).aggregate(total=Sum("reserved_cost"))["total"] or decimal.Decimal("0")
    return audit_total + reserved_total
