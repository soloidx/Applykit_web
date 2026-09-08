import decimal
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from django.db import close_old_connections, connection
from django.utils import timezone

from apps.accounts.models import Account
from apps.ai import admission, engine
from apps.ai.conf import FEATURE_CANDIDATE_PROFILE, FEATURE_JOB_POSTING
from apps.ai.errors import INTERNAL_ERROR, RATE_LIMITED, UNAVAILABLE, AIError
from apps.ai.models import AIOperationAudit, AIOperationReservation, AIOperationSwitch
from apps.ai.retention import purge_expired
from apps.ai.services import accept_consent
from tests.integration.ai_test_support import ai_overrides, enable_switches
from tests.unit.ai_support import FakeTransport

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

SOURCE = "Jane Doe is a Senior Engineer skilled in Node.js and Rust."


def verified_candidate(email: str) -> Account:
    return Account.objects.create_user(email, "a-secure-password")


def consenting_candidate(email: str) -> Account:
    account = verified_candidate(email)
    accept_consent(account=account)
    return account


@pytest.fixture(autouse=True)
def enabled_ai_settings():
    from django.test import override_settings

    with override_settings(AI_IMPORTS=ai_overrides()):
        yield


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch):
    class Wire:
        def __init__(self) -> None:
            self.fakes: list[FakeTransport] = []

        def install(self, *outcomes: object) -> FakeTransport:
            fake = FakeTransport(*outcomes)
            self.fakes.append(fake)
            monkeypatch.setattr(engine, "_client_for", lambda _config: fake)
            return fake

    original = engine._client_for
    yield Wire()
    engine._client_for = original  # type: ignore[method-assign]


def config():
    from apps.ai.conf import feature_config

    return feature_config(FEATURE_CANDIDATE_PROFILE)


class TestSwitches:
    def test_absent_switches_disable_every_operation(self) -> None:
        account = consenting_candidate("switch-absent@example.com")

        with pytest.raises(AIError) as raised:
            admission.admit(account, config())

        assert raised.value.category == UNAVAILABLE
        assert not AIOperationReservation.objects.filter(account=account).exists()

    def test_global_switch_alone_is_not_enough(self) -> None:
        account = consenting_candidate("switch-global@example.com")
        AIOperationSwitch.objects.create(scope="global", enabled=True)

        with pytest.raises(AIError) as raised:
            admission.admit(account, config())

        assert raised.value.category == UNAVAILABLE

    def test_feature_switch_alone_is_not_enough(self) -> None:
        account = consenting_candidate("switch-feature@example.com")
        AIOperationSwitch.objects.create(
            scope=FEATURE_CANDIDATE_PROFILE,
            enabled=True,
        )

        with pytest.raises(AIError) as raised:
            admission.admit(account, config())

        assert raised.value.category == UNAVAILABLE

    def test_switches_are_scope_isolated_per_feature(self) -> None:
        account = consenting_candidate("switch-scope@example.com")
        AIOperationSwitch.objects.create(scope="global", enabled=True)
        AIOperationSwitch.objects.create(scope=FEATURE_JOB_POSTING, enabled=True)

        with pytest.raises(AIError) as raised:
            admission.admit(account, config())

        assert raised.value.category == UNAVAILABLE

    def test_disabled_switch_stops_operations_immediately(self) -> None:
        account = consenting_candidate("switch-off@example.com")
        enable_switches()
        admission.admit(account, config())

        AIOperationSwitch.objects.filter(scope="global").update(enabled=False)

        with pytest.raises(AIError) as raised:
            admission.admit(account, config())

        assert raised.value.category == UNAVAILABLE


class TestAdmission:
    def test_enabled_switches_admit_one_in_flight_operation(self) -> None:
        account = consenting_candidate("admit@example.com")
        enable_switches()

        reservation = admission.admit(account, config())

        assert reservation.feature == FEATURE_CANDIDATE_PROFILE
        assert reservation.reserved_cost == decimal.Decimal("0.50")
        row = AIOperationReservation.objects.get(pk=reservation.id)
        assert row.status == AIOperationReservation.STATUS_IN_FLIGHT
        assert row.reserved_cost == decimal.Decimal("0.50")

    def test_second_in_flight_operation_is_rate_limited(self) -> None:
        account = consenting_candidate("in-flight@example.com")
        enable_switches()
        first = admission.admit(account, config())

        with pytest.raises(AIError) as raised:
            admission.admit(account, config())

        assert raised.value.category == RATE_LIMITED
        assert AIOperationReservation.objects.filter(account=account).count() == 1
        assert AIOperationReservation.objects.get(pk=first.id).status == (
            AIOperationReservation.STATUS_IN_FLIGHT
        )

    def test_reconcile_releases_the_in_flight_slot(self) -> None:
        account = consenting_candidate("reconcile@example.com")
        enable_switches()
        first = admission.admit(account, config())

        admission.reconcile(first)

        row = AIOperationReservation.objects.get(pk=first.id)
        assert row.status == AIOperationReservation.STATUS_COMPLETED

        second = admission.admit(account, config())
        assert second.id != first.id

    def test_reconcile_is_idempotent(self) -> None:
        account = consenting_candidate("reconcile-idempotent@example.com")
        enable_switches()
        reservation = admission.admit(account, config())
        admission.reconcile(reservation)

        admission.reconcile(reservation)

        row = AIOperationReservation.objects.get(pk=reservation.id)
        assert row.status == AIOperationReservation.STATUS_COMPLETED

    def test_old_in_flight_reservation_still_blocks_a_second_operation(self) -> None:
        account = consenting_candidate("old-in-flight@example.com")
        enable_switches()
        admission.admit(account, config())

        with pytest.raises(AIError) as raised:
            admission.admit(account, config())

        assert raised.value.category == RATE_LIMITED


def started_reservation(account: Account, *, age: timedelta | None = None) -> None:
    row = AIOperationReservation.objects.create(
        account=account,
        feature=FEATURE_CANDIDATE_PROFILE,
        status=AIOperationReservation.STATUS_COMPLETED,
    )
    if age is not None:
        AIOperationReservation.objects.filter(pk=row.pk).update(created_at=timezone.now() - age)


class TestRollingWindows:
    def test_five_started_operations_per_rolling_15_minutes(self) -> None:
        account = consenting_candidate("rolling-15@example.com")
        enable_switches()
        for _ in range(5):
            started_reservation(account)

        with pytest.raises(AIError) as raised:
            admission.admit(account, config())

        assert raised.value.category == RATE_LIMITED

    def test_twenty_started_operations_per_rolling_24_hours(self) -> None:
        account = consenting_candidate("rolling-24@example.com")
        enable_switches()
        for _ in range(5):
            started_reservation(account, age=timedelta(minutes=16))
        for _ in range(15):
            started_reservation(account, age=timedelta(hours=2))

        with pytest.raises(AIError) as raised:
            admission.admit(account, config())

        assert raised.value.category == RATE_LIMITED

    def test_started_operations_outside_the_windows_do_not_count(self) -> None:
        account = consenting_candidate("rolling-expired@example.com")
        enable_switches()
        for _ in range(5):
            started_reservation(account, age=timedelta(minutes=16))
        for _ in range(15):
            started_reservation(account, age=timedelta(hours=25))

        assert admission.admit(account, config()) is not None

    def test_started_operations_are_isolated_per_account(self) -> None:
        account = consenting_candidate("rolling-isolated@example.com")
        other = consenting_candidate("rolling-isolated-other@example.com")
        enable_switches()
        for _ in range(5):
            started_reservation(other)

        assert admission.admit(account, config()) is not None


class TestBudget:
    def test_outstanding_reserves_count_against_the_account_ceiling(self) -> None:
        account = consenting_candidate("budget-reserve@example.com")
        enable_switches()
        AIOperationAudit.objects.create(
            account=account,
            feature=FEATURE_CANDIDATE_PROFILE,
            consent_policy="test-policy",
            outcome=AIOperationAudit.OUTCOME_SUCCESS,
            cost=decimal.Decimal("4.60"),
        )

        with pytest.raises(AIError) as raised:
            admission.admit(account, config())

        assert raised.value.category == RATE_LIMITED

    def test_audits_outside_the_rolling_30_day_window_do_not_count(self) -> None:
        account = consenting_candidate("budget-window@example.com")
        enable_switches()
        audit = AIOperationAudit.objects.create(
            account=account,
            feature=FEATURE_CANDIDATE_PROFILE,
            consent_policy="test-policy",
            outcome=AIOperationAudit.OUTCOME_SUCCESS,
            cost=decimal.Decimal("4.60"),
        )
        AIOperationAudit.objects.filter(pk=audit.pk).update(
            created_at=timezone.now() - timedelta(days=31)
        )

        reservation = admission.admit(account, config())

        assert reservation.reserved_cost == decimal.Decimal("0.50")


class TestOperatorExposure:
    def test_operators_toggle_switches_from_the_admin(self) -> None:
        from django.test import Client
        from django.urls import reverse

        administrator = Account.objects.create_superuser(
            "operator@example.com", "a-secure-password"
        )
        switch = AIOperationSwitch.objects.create(scope="global", enabled=False)
        client = Client()
        client.force_login(administrator)

        changelist = client.get(reverse("admin:ai_aioperationswitch_changelist"))
        assert changelist.status_code == 200
        assert "global" in changelist.content.decode()

        enabled = client.post(
            reverse("admin:ai_aioperationswitch_change", args=[switch.pk]),
            {"scope": "global", "enabled": "on"},
        )
        assert enabled.status_code == 302
        assert AIOperationSwitch.objects.get(pk=switch.pk).enabled is True

    def test_reservations_are_visible_but_read_only_in_the_admin(self) -> None:
        from django.test import Client
        from django.urls import reverse

        administrator = Account.objects.create_superuser(
            "operator-reservations@example.com", "a-secure-password"
        )
        account = consenting_candidate("operator-owned@example.com")
        enable_switches()
        reservation = admission.admit(account, config())
        client = Client()
        client.force_login(administrator)

        changelist = client.get(reverse("admin:ai_aioperationreservation_changelist"))
        assert changelist.status_code == 200

        detail = client.get(
            reverse("admin:ai_aioperationreservation_change", args=[reservation.id])
        )
        assert detail.status_code == 200
        content = detail.content.decode()
        assert "in_flight" in content
        assert 'name="status"' not in content


class TestConcurrency:
    # Concurrent admission races are only meaningful against PostgreSQL, the
    # database the production integration suite runs against.
    pytestmark = pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="concurrent admission races require PostgreSQL",
    )

    @pytest.mark.django_db(transaction=True)
    def test_concurrent_admissions_allow_only_one_in_flight_per_account(self) -> None:
        account = consenting_candidate("race-in-flight@example.com")
        enable_switches()
        barrier = Barrier(2)

        def admit() -> str:
            close_old_connections()
            try:
                barrier.wait()
                admission.admit(account, config())
                return "admitted"
            except AIError as rejection:
                return rejection.category
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: admit(), range(2)))

        assert sorted(results) == ["admitted", "rate_limited"]
        rows = AIOperationReservation.objects.filter(account=account)
        assert rows.filter(status=AIOperationReservation.STATUS_IN_FLIGHT).count() == 1

    @pytest.mark.django_db(transaction=True)
    def test_concurrent_admissions_are_isolated_per_account(self) -> None:
        first = consenting_candidate("race-first@example.com")
        second = consenting_candidate("race-second@example.com")
        enable_switches()
        barrier = Barrier(2)

        def admit(account: Account) -> str:
            close_old_connections()
            try:
                barrier.wait()
                admission.admit(account, config())
                return "admitted"
            except AIError as rejection:
                return rejection.category
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(admit, [first, second]),
            )

        assert results == ["admitted", "admitted"]
        assert (
            AIOperationReservation.objects.filter(
                account=first, status=AIOperationReservation.STATUS_IN_FLIGHT
            ).count()
            == 1
        )
        assert (
            AIOperationReservation.objects.filter(
                account=second, status=AIOperationReservation.STATUS_IN_FLIGHT
            ).count()
            == 1
        )

    @pytest.mark.django_db(transaction=True)
    def test_concurrent_reconciliation_releases_each_account_slot(self) -> None:
        first = consenting_candidate("race-reconcile-first@example.com")
        second = consenting_candidate("race-reconcile-second@example.com")
        enable_switches()

        def cycle(account: Account) -> str:
            close_old_connections()
            try:
                reservation = admission.admit(account, config())
                admission.reconcile(reservation)
                return "completed"
            except AIError as rejection:
                return rejection.category
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(cycle, [first, second]))

        assert results == ["completed", "completed"]
        assert (
            AIOperationReservation.objects.filter(
                status=AIOperationReservation.STATUS_COMPLETED
            ).count()
            == 2
        )

    @pytest.mark.django_db(transaction=True)
    def test_concurrent_disabled_switches_fail_closed(self) -> None:
        first = consenting_candidate("race-switch-first@example.com")
        second = consenting_candidate("race-switch-second@example.com")
        barrier = Barrier(2)

        def admit(account: Account) -> str:
            close_old_connections()
            try:
                barrier.wait()
                admission.admit(account, config())
                return "admitted"
            except AIError as rejection:
                return rejection.category
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(admit, [first, second]))

        assert results == [UNAVAILABLE, UNAVAILABLE]
        assert not AIOperationReservation.objects.exists()

    @pytest.mark.django_db(transaction=True)
    def test_concurrent_unavailable_budget_state_fails_closed(self, monkeypatch) -> None:
        first = consenting_candidate("race-budget-first@example.com")
        second = consenting_candidate("race-budget-second@example.com")
        enable_switches()
        barrier = Barrier(2)

        def unavailable(*_args: object, **_kwargs: object) -> decimal.Decimal:
            raise RuntimeError("budget unavailable")

        monkeypatch.setattr(admission, "_budget_spent", unavailable)

        def admit(account: Account) -> str:
            close_old_connections()
            try:
                barrier.wait()
                admission.admit(account, config())
                return "admitted"
            except AIError as rejection:
                return rejection.category
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(admit, [first, second]))

        assert results == [INTERNAL_ERROR, INTERNAL_ERROR]
        assert not AIOperationReservation.objects.exists()

    @pytest.mark.django_db(transaction=True)
    def test_purge_runs_alongside_admission_without_losing_fresh_records(self) -> None:
        account = consenting_candidate("race-purge@example.com")
        enable_switches()
        barrier = Barrier(2)

        def admit() -> str:
            close_old_connections()
            try:
                barrier.wait()
                admission.admit(account, config())
                return "admitted"
            except AIError as rejection:
                return rejection.category
            finally:
                close_old_connections()

        def purge() -> int:
            close_old_connections()
            try:
                barrier.wait()
                return purge_expired()
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            admitted = executor.submit(admit)
            purged = executor.submit(purge)
            assert admitted.result() == "admitted"
            assert purged.result() == 0

        assert (
            AIOperationReservation.objects.filter(
                account=account, status=AIOperationReservation.STATUS_IN_FLIGHT
            ).count()
            == 1
        )
