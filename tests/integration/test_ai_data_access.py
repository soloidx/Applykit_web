import decimal

import pytest
from allauth.account.models import EmailAddress
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Account
from apps.ai.conf import FEATURE_CANDIDATE_PROFILE
from apps.ai.data_access import account_ai_data
from apps.ai.models import AIOperationAudit
from apps.ai.services import ConsentStatus, accept_consent

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


def verified_candidate(email: str) -> Account:
    account = Account.objects.create_user(email, "a-secure-password")
    EmailAddress.objects.create(user=account, email=account.email, primary=True, verified=True)
    return account


def audit_for(account: Account, *, feature: str = FEATURE_CANDIDATE_PROFILE) -> AIOperationAudit:
    return AIOperationAudit.objects.create(
        account=account,
        feature=feature,
        consent_policy="test-policy",
        outcome=AIOperationAudit.OUTCOME_SUCCESS,
        model="acme/profile-model",
        route="openrouter/acme",
        prompt_tokens=100,
        completion_tokens=50,
        cost=decimal.Decimal("0.02"),
    )


class TestService:
    def test_output_includes_consent_and_content_free_audits(self) -> None:
        account = verified_candidate("data@example.com")
        accept_consent(account=account)
        audit_for(account)

        data = account_ai_data(account)

        assert data.consent is ConsentStatus.CURRENT
        assert data.consent_policy
        assert len(data.audits) == 1
        audit = data.audits[0]
        assert audit.feature == FEATURE_CANDIDATE_PROFILE
        assert audit.consent_policy == "test-policy"
        assert audit.outcome == AIOperationAudit.OUTCOME_SUCCESS
        assert audit.model == "acme/profile-model"
        assert audit.route == "openrouter/acme"
        assert audit.prompt_tokens == 100
        assert audit.completion_tokens == 50
        assert audit.cost == decimal.Decimal("0.02")
        assert audit.created_at is not None

    def test_audits_are_scoped_to_the_owning_account(self) -> None:
        account = verified_candidate("data-scoped@example.com")
        audit_for(account)
        other = verified_candidate("data-other@example.com")

        data = account_ai_data(other)

        assert data.audits == ()

    def test_output_explains_why_source_and_raw_outputs_are_unavailable(self) -> None:
        account = verified_candidate("data-explain@example.com")
        audit_for(account)

        data = account_ai_data(account)

        explanation = data.content_explanation.lower()
        assert "source content" in explanation
        assert "raw" in explanation
        assert "never" in explanation
        assert "never stores" in explanation

    def test_absent_consent_is_reported_as_not_accepted(self) -> None:
        account = verified_candidate("data-no-consent@example.com")

        data = account_ai_data(account)

        assert data.consent is ConsentStatus.NOT_ACCEPTED
        assert data.audits == ()


class TestView:
    def test_data_access_requires_a_verified_account(self) -> None:
        anonymous = Client()

        assert anonymous.get(reverse("ai_data_access")).status_code == 302

    def test_data_access_page_shows_consent_audits_and_explanation(self) -> None:
        account = verified_candidate("data-view@example.com")
        accept_consent(account=account)
        audit_for(account)
        client = Client()
        client.force_login(account)

        response = client.get(reverse("ai_data_access"))

        assert response.status_code == 200
        content = response.content.decode()
        assert "AI data" in content
        assert "accepted" in content.lower()
        assert FEATURE_CANDIDATE_PROFILE in content
        assert "success" in content
        assert "0.02" in content
        assert "never" in content.lower()

    def test_data_access_rejects_other_methods(self) -> None:
        account = verified_candidate("data-method@example.com")
        client = Client()
        client.force_login(account)

        assert client.post(reverse("ai_data_access")).status_code == 405
