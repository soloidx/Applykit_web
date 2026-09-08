from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.accounts.models import Account
from apps.accounts.services import delete_account
from apps.ai.conf import FEATURE_CANDIDATE_PROFILE
from apps.ai.models import AIOperationAudit, AIOperationReservation, ConsentPreference
from apps.ai.retention import purge_expired
from apps.ai.services import accept_consent

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


def verified_candidate(email: str) -> Account:
    return Account.objects.create_user(email, "a-secure-password")


def audit_for(account: Account, *, age_days: int, cost: str = "0.00") -> AIOperationAudit:
    audit = AIOperationAudit.objects.create(
        account=account,
        feature=FEATURE_CANDIDATE_PROFILE,
        consent_policy="test-policy",
        outcome=AIOperationAudit.OUTCOME_SUCCESS,
        cost=cost,
    )
    AIOperationAudit.objects.filter(pk=audit.pk).update(
        created_at=timezone.now() - timedelta(days=age_days)
    )
    return AIOperationAudit.objects.get(pk=audit.pk)


def reservation_for(account: Account, *, age_days: int) -> AIOperationReservation:
    reservation = AIOperationReservation.objects.create(
        account=account,
        feature=FEATURE_CANDIDATE_PROFILE,
        status=AIOperationReservation.STATUS_COMPLETED,
    )
    AIOperationReservation.objects.filter(pk=reservation.pk).update(
        created_at=timezone.now() - timedelta(days=age_days)
    )
    return AIOperationReservation.objects.get(pk=reservation.pk)


def test_purge_deletes_only_audits_older_than_twelve_months() -> None:
    account = verified_candidate("purge@example.com")
    old = audit_for(account, age_days=380)
    fresh = audit_for(account, age_days=180)

    deleted = purge_expired()

    assert deleted >= 1
    assert not AIOperationAudit.objects.filter(pk=old.pk).exists()
    assert AIOperationAudit.objects.filter(pk=fresh.pk).exists()


def test_purge_is_idempotent() -> None:
    account = verified_candidate("purge-idempotent@example.com")
    audit_for(account, age_days=380)
    assert purge_expired() > 0

    assert purge_expired() == 0


def test_purge_management_command_runs_the_same_operation() -> None:
    account = verified_candidate("purge-command@example.com")
    audit_for(account, age_days=380)

    call_command("purge_ai_audits")

    assert not AIOperationAudit.objects.filter(account=account).exists()


def test_account_deletion_removes_reservations_audits_and_consent() -> None:
    account = verified_candidate("retention-delete@example.com")
    accept_consent(account=account)
    audit_for(account, age_days=1)
    reservation_for(account, age_days=1)
    assert ConsentPreference.objects.filter(account=account).exists()

    delete_account(account=account)

    assert not AIOperationAudit.objects.filter(account=account).exists()
    assert not AIOperationReservation.objects.filter(account=account).exists()
    assert not ConsentPreference.objects.filter(account=account).exists()
    assert not Account.objects.filter(pk=account.pk).exists()
