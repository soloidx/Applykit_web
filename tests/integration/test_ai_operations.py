import decimal
import json
import logging
from datetime import timedelta
from typing import Any

import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from apps.accounts.models import Account
from apps.ai import engine, operations, transport
from apps.ai.conf import FEATURE_CANDIDATE_PROFILE
from apps.ai.errors import (
    CONFIGURATION_ERROR,
    CONSENT_REQUIRED,
    INTERNAL_ERROR,
    INVALID_INPUT,
    RATE_LIMITED,
    UNAVAILABLE,
    AIError,
)
from apps.ai.models import AIOperationAudit, AIOperationReservation, AIOperationSwitch
from apps.ai.schemas import JobPostingExtraction
from apps.ai.services import accept_consent, withdraw_consent
from tests.integration.ai_test_support import ai_overrides, enable_switches
from tests.unit.ai_support import (
    FakeTransport,
    completion_body,
    profile_payload,
)

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

SOURCE = "Jane Doe is a Senior Engineer skilled in Node.js and Rust."


def verified_candidate(email: str) -> Account:
    return Account.objects.create_user(email, "a-secure-password")


class Wire:
    """Scripted fake transport installer for one test."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._monkeypatch = monkeypatch
        self.fakes: list[FakeTransport] = []

    def install(self, *outcomes: object) -> FakeTransport:
        fake = FakeTransport(*outcomes)
        self.fakes.append(fake)
        self._monkeypatch.setattr(engine, "_client_for", lambda _config: fake)
        return fake


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Wire:
    return Wire(monkeypatch)


@pytest.fixture(autouse=True)
def enabled_ai_settings():
    with override_settings(AI_IMPORTS=ai_overrides()):
        enable_switches()
        yield


@pytest.fixture(autouse=True)
def restore_client():
    original = engine._client_for
    yield
    engine._client_for = original  # type: ignore[method-assign]


def consenting_candidate(email: str) -> Account:
    account = verified_candidate(email)
    accept_consent(account=account)
    return account


class TestSuccessfulExtraction:
    def test_profile_operation_returns_validated_facts(self, wire) -> None:
        account = consenting_candidate("success@example.com")
        grounded_skill = {
            "source_wording": "Node.js",
            "proposed_concept": "Node.js",
            "suitability": "suitable",
            "status": "resolved",
        }
        wire.install(completion_body(content=json.dumps(profile_payload(skills=[grounded_skill]))))

        extraction = operations.extract_candidate_profile(account, SOURCE)

        assert extraction.full_name == "Jane Doe"
        assert extraction.skills[0].source_wording == "Node.js"

    def test_posting_operation_uses_its_own_schema_and_model(self, wire) -> None:
        account = consenting_candidate("posting@example.com")
        posting = {
            "company_name": None,
            "company_website": None,
            "role_title": "Backend Engineer",
            "job_description": "Build services with Node.js.",
            "location": None,
            "compensation": None,
            "posting_url": None,
            "requirements": [
                {
                    "source_wording": "Node.js",
                    "proposed_concept": "Node.js",
                    "suitability": "suitable",
                    "status": "resolved",
                    "classification": "required",
                }
            ],
        }
        fake = wire.install(completion_body(content=json.dumps(posting)))

        extraction = operations.extract_job_posting(account, "Build services with Node.js.")

        assert isinstance(extraction, JobPostingExtraction)
        assert extraction.role_title == "Backend Engineer"
        payload = fake.calls[0][0]
        assert payload["model"] == "acme/posting-model"
        assert payload["response_format"]["json_schema"]["name"] == "job_posting_extraction_v1"

    def test_request_carries_privacy_route_and_bounds(self, wire) -> None:
        account = consenting_candidate("privacy@example.com")
        fake = wire.install(completion_body(content=json.dumps(profile_payload())))

        operations.extract_candidate_profile(account, SOURCE)

        payload = fake.calls[0][0]
        provider = payload["provider"]
        assert provider["require_parameters"] is True
        assert provider["data_collection"] == "deny"
        assert provider["zdr"] is True
        assert provider["max_price"] == 0.50
        assert payload["response_format"]["json_schema"]["strict"] is True
        assert payload["stream"] is False
        assert payload["max_tokens"] == 8000
        # The source text travels as bounded canonical input only.
        user_text = payload["messages"][1]["content"]
        assert SOURCE in user_text

    def test_canonical_text_is_normalized_before_transmission(self, wire) -> None:
        account = consenting_candidate("nfc@example.com")
        posting = {
            "company_name": None,
            "company_website": None,
            "role_title": None,
            "job_description": None,
            "location": None,
            "compensation": None,
            "posting_url": None,
            "requirements": [],
        }
        fake = wire.install(completion_body(content=json.dumps(posting)))
        decomposed = "Node.js caf\u00e9\x00 text\twith controls\r\nlines"

        operations.extract_job_posting(account, decomposed)

        user_text = fake.calls[0][0]["messages"][1]["content"]
        assert "\x00" not in user_text
        assert "\r" not in user_text
        assert "café" in user_text

    def test_exactly_one_audit_with_aggregate_usage_and_cost(self, wire) -> None:
        account = consenting_candidate("audit@example.com")
        invalid = json.dumps(profile_payload(skills="nope"))
        wire.install(
            completion_body(content=invalid, model="acme/route-a"),
            completion_body(content=json.dumps(profile_payload()), model="acme/route-b"),
        )

        operations.extract_candidate_profile(account, SOURCE)

        audits = AIOperationAudit.objects.filter(account=account)
        assert audits.count() == 1
        audit = audits.get()
        assert audit.outcome == AIOperationAudit.OUTCOME_SUCCESS
        assert audit.feature == FEATURE_CANDIDATE_PROFILE
        assert audit.consent_policy == settings.AI_CONSENT["POLICY"]
        assert audit.model == "acme/route-b"
        assert audit.prompt_tokens == 200
        assert audit.completion_tokens == 100
        assert audit.cost == pytest.approx(decimal.Decimal("0.04"))

    def test_success_fails_closed_when_the_audit_cannot_persist(self, wire, monkeypatch) -> None:
        account = consenting_candidate("audit-failure@example.com")
        wire.install(completion_body(content=json.dumps(profile_payload())))

        def broken(**_kwargs: object) -> None:
            raise RuntimeError("storage unavailable")

        monkeypatch.setattr(AIOperationAudit.objects, "create", broken)

        with pytest.raises(AIError) as raised:
            operations.extract_candidate_profile(account, SOURCE)

        assert raised.value.category == INTERNAL_ERROR
        assert AIOperationAudit.objects.filter(account=account).count() == 0


class TestSafeFailures:
    def test_consent_required_blocks_before_any_transmission(self, wire) -> None:
        account = verified_candidate("no-consent@example.com")
        fake = wire.install(completion_body(content=json.dumps(profile_payload())))

        with pytest.raises(AIError) as raised:
            operations.extract_candidate_profile(account, SOURCE)

        assert raised.value.category == CONSENT_REQUIRED
        assert fake.call_count == 0
        audit = AIOperationAudit.objects.get(account=account)
        assert audit.outcome == CONSENT_REQUIRED
        assert audit.prompt_tokens == 0
        assert audit.cost == 0

    def test_withdrawn_consent_blocks_immediately(self, wire) -> None:
        account = consenting_candidate("withdrawn@example.com")
        withdraw_consent(account=account)
        fake = wire.install(completion_body(content=json.dumps(profile_payload())))

        with pytest.raises(AIError) as raised:
            operations.extract_candidate_profile(account, SOURCE)

        assert raised.value.category == CONSENT_REQUIRED
        assert fake.call_count == 0

    def test_stale_consent_policy_requires_renewal(self, wire) -> None:
        account = verified_candidate("stale-policy@example.com")
        accept_consent(account=account)
        fake = wire.install(completion_body(content=json.dumps(profile_payload())))
        with override_settings(AI_CONSENT={"POLICY": "new-policy-v2"}):
            with pytest.raises(AIError) as raised:
                operations.extract_candidate_profile(account, SOURCE)

        assert raised.value.category == CONSENT_REQUIRED
        assert fake.call_count == 0

    def test_invalid_input_never_reaches_the_provider(self, wire) -> None:
        account = consenting_candidate("invalid-input@example.com")
        fake = wire.install(completion_body(content=json.dumps(profile_payload())))

        for text in ("", "   ", "\x00\x1f", "x" * 50_001):
            with pytest.raises(AIError) as raised:
                operations.extract_job_posting(account, text)
            assert raised.value.category == INVALID_INPUT

        assert fake.call_count == 0
        assert AIOperationAudit.objects.filter(account=account, outcome=INVALID_INPUT).count() == 4

    def test_input_token_limit_is_enforced_locally(self, wire, settings) -> None:
        account = consenting_candidate("token-limit@example.com")
        fake = wire.install(completion_body(content=json.dumps(profile_payload())))
        settings.AI_IMPORTS = ai_overrides(MAX_INPUT_TOKENS=10)

        with pytest.raises(AIError) as raised:
            operations.extract_candidate_profile(account, "word " * 100)

        assert raised.value.category == INVALID_INPUT
        assert fake.call_count == 0

    def test_configuration_error_fails_closed(self, wire, settings) -> None:
        account = consenting_candidate("config@example.com")
        fake = wire.install(completion_body(content=json.dumps(profile_payload())))
        settings.AI_IMPORTS = ai_overrides(API_KEY="")

        with pytest.raises(AIError) as raised:
            operations.extract_candidate_profile(account, SOURCE)

        assert raised.value.category == CONFIGURATION_ERROR
        assert fake.call_count == 0
        audit = AIOperationAudit.objects.get(account=account)
        assert audit.outcome == CONFIGURATION_ERROR

    def test_configuration_validation_does_not_break_startup(self, settings) -> None:
        settings.AI_IMPORTS = ai_overrides(API_KEY="")
        call_command("check")

    def test_provider_unavailable_is_safe_and_audited(self, wire) -> None:
        account = consenting_candidate("unavailable@example.com")
        wire.install(transport_result(b"{}", status=503), transport_result(b"{}", status=503))

        with pytest.raises(AIError) as raised:
            operations.extract_candidate_profile(account, SOURCE)

        assert raised.value.category == UNAVAILABLE
        audit = AIOperationAudit.objects.get(account=account)
        assert audit.outcome == UNAVAILABLE
        assert audit.model == "acme/profile-model"
        assert audit.prompt_tokens == 0

    def test_rate_limited_after_retry_is_safe_and_audited(self, wire) -> None:
        account = consenting_candidate("rate@example.com")
        wire.install(transport_result(b"{}", status=429), transport_result(b"{}", status=429))

        with pytest.raises(AIError) as raised:
            operations.extract_candidate_profile(account, SOURCE)

        assert raised.value.category == RATE_LIMITED
        audit = AIOperationAudit.objects.get(account=account)
        assert audit.outcome == RATE_LIMITED

    def test_failure_audit_persistence_failure_returns_internal_error(
        self, wire, monkeypatch
    ) -> None:
        account = consenting_candidate("fail-audit@example.com")
        wire.install(transport_result(b"{}", status=503), transport_result(b"{}", status=503))

        def broken(**_kwargs: object) -> None:
            raise RuntimeError("storage unavailable")

        monkeypatch.setattr(AIOperationAudit.objects, "create", broken)

        with pytest.raises(AIError) as raised:
            operations.extract_candidate_profile(account, SOURCE)

        assert raised.value.category == INTERNAL_ERROR


def transport_result(body: bytes, status: int = 200) -> Any:

    return transport.TransportResult(status=status, headers={}, body=body)


class TestPrivacyAndLifecycle:
    def test_audit_records_are_content_free(self, wire) -> None:
        account = consenting_candidate("content-free@example.com")
        secret = "CONFIDENTIAL-project-Nova-details"
        wire.install(completion_body(content=json.dumps(profile_payload())))

        operations.extract_candidate_profile(account, f"{SOURCE} {secret}")

        audit = AIOperationAudit.objects.get(account=account)
        fields = [str(value) for value in vars(audit).values()] + [str(audit)]
        joined = " ".join(fields)
        assert secret not in joined
        assert SOURCE not in joined

    def test_account_deletion_cascades_audits(self, wire) -> None:
        account = consenting_candidate("deletion@example.com")
        wire.install(completion_body(content=json.dumps(profile_payload())))
        operations.extract_candidate_profile(account, SOURCE)
        assert AIOperationAudit.objects.filter(account=account).exists()

        account.delete()

        assert AIOperationAudit.objects.count() == 0

    def test_operations_are_scoped_to_the_calling_account(self, wire) -> None:
        owner = consenting_candidate("owner@example.com")
        wire.install(completion_body(content=json.dumps(profile_payload())))

        operations.extract_candidate_profile(owner, SOURCE)

        other = verified_candidate("other@example.com")
        assert not AIOperationAudit.objects.filter(account=other).exists()


class TestUsageBounds:
    def test_disabled_switches_block_operations_without_any_provider_request(self, wire) -> None:
        account = consenting_candidate("switch-blocked@example.com")
        AIOperationSwitch.objects.all().delete()
        fake = wire.install(completion_body(content=json.dumps(profile_payload())))

        with pytest.raises(AIError) as raised:
            operations.extract_candidate_profile(account, SOURCE)

        assert raised.value.category == UNAVAILABLE
        assert fake.call_count == 0
        audit = AIOperationAudit.objects.get(account=account)
        assert audit.outcome == UNAVAILABLE

    def test_exhausted_rolling_window_is_audited_without_any_provider_request(self, wire) -> None:
        account = consenting_candidate("rolling-blocked@example.com")
        for _ in range(5):
            AIOperationReservation.objects.create(
                account=account,
                feature=FEATURE_CANDIDATE_PROFILE,
                status=AIOperationReservation.STATUS_COMPLETED,
            )
        fake = wire.install(completion_body(content=json.dumps(profile_payload())))

        with pytest.raises(AIError) as raised:
            operations.extract_candidate_profile(account, SOURCE)

        assert raised.value.category == RATE_LIMITED
        assert fake.call_count == 0
        audit = AIOperationAudit.objects.get(account=account)
        assert audit.outcome == RATE_LIMITED

    def test_budget_reservation_and_reconciliation_bound_spend(self, wire) -> None:
        account = consenting_candidate("budget@example.com")
        AIOperationAudit.objects.create(
            account=account,
            feature=FEATURE_CANDIDATE_PROFILE,
            consent_policy="test-policy",
            outcome=AIOperationAudit.OUTCOME_SUCCESS,
            cost=decimal.Decimal("4.50"),
        )

        wire.install(completion_body(content=json.dumps(profile_payload())))
        operations.extract_candidate_profile(account, SOURCE)
        assert AIOperationAudit.objects.filter(account=account).count() == 2
        reservation = AIOperationReservation.objects.get(account=account)
        assert reservation.status == AIOperationReservation.STATUS_COMPLETED

        # The remaining ceiling can no longer cover a fresh per-request reserve.
        fake = wire.install(completion_body(content=json.dumps(profile_payload())))
        with pytest.raises(AIError) as raised:
            operations.extract_candidate_profile(account, SOURCE)

        assert raised.value.category == RATE_LIMITED
        assert fake.call_count == 0

    def test_older_spend_falls_out_of_the_rolling_window(self, wire) -> None:
        account = consenting_candidate("budget-window@example.com")
        spent = AIOperationAudit.objects.create(
            account=account,
            feature=FEATURE_CANDIDATE_PROFILE,
            consent_policy="test-policy",
            outcome=AIOperationAudit.OUTCOME_SUCCESS,
            cost=decimal.Decimal("4.60"),
        )
        spent.created_at = timezone.now() - timedelta(days=45)
        spent.save()

        fake = wire.install(completion_body(content=json.dumps(profile_payload())))

        operations.extract_candidate_profile(account, SOURCE)

        assert fake.call_count == 1

    def test_unavailable_budget_state_fails_closed(self, wire, monkeypatch) -> None:
        account = consenting_candidate("budget-closed@example.com")
        fake = wire.install(completion_body(content=json.dumps(profile_payload())))

        def broken(*_args: object, **_kwargs: object) -> object:
            raise RuntimeError("budget state unavailable")

        monkeypatch.setattr("django.db.models.query.QuerySet.aggregate", broken)

        with pytest.raises(AIError) as raised:
            operations.extract_candidate_profile(account, SOURCE)

        assert raised.value.category == INTERNAL_ERROR
        assert fake.call_count == 0
        audit = AIOperationAudit.objects.get(account=account)
        assert audit.outcome == INTERNAL_ERROR

    def test_operation_telemetry_is_content_free(self, wire, caplog) -> None:
        account = consenting_candidate("telemetry@example.com")
        secret = "CONFIDENTIAL-project-Nova-details"
        wire.install(completion_body(content=json.dumps(profile_payload())))

        with caplog.at_level(logging.INFO):
            operations.extract_candidate_profile(account, f"{SOURCE} {secret}")

        assert secret not in caplog.text
        assert SOURCE not in caplog.text
        records = [
            record
            for record in caplog.records
            if record.name in ("applykit.ai", "applykit.security")
        ]
        completed = [r for r in records if r.event == "ai_operation_completed"]
        assert len(completed) == 1
        record = completed[0]
        assert record.account_id == account.pk
        assert record.feature == FEATURE_CANDIDATE_PROFILE
        assert record.outcome == AIOperationAudit.OUTCOME_SUCCESS
        assert record.attempts == 1
        assert record.duration_ms >= 0
        assert record.prompt_tokens == 100
        assert record.completion_tokens == 50
