import pytest
from allauth.account.models import EmailAddress
from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.accounts.models import Account
from apps.accounts.services import delete_account
from apps.ai.access import ai_consent_required
from apps.ai.conf import current_policy
from apps.ai.models import ConsentPreference
from apps.ai.services import ConsentStatus, consent_status, has_current_consent
from apps.campaigns.models import Campaign

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


def verified_candidate(email: str) -> Account:
    account = Account.objects.create_user(email, "a-secure-password")
    EmailAddress.objects.create(user=account, email=account.email, primary=True, verified=True)
    return account


def unverified_candidate(email: str) -> Account:
    account = Account.objects.create_user(email, "a-secure-password")
    EmailAddress.objects.create(user=account, email=account.email, primary=True, verified=False)
    return account


def gated_probe(account: Account) -> HttpResponse:
    @ai_consent_required
    def view(request: HttpRequest) -> HttpResponse:
        return HttpResponse("ai content")

    request = RequestFactory().get("/ai/import/")
    request.user = account
    return view(request)


def preference_for(account: Account) -> ConsentPreference:
    return ConsentPreference.objects.get(account=account)


def test_absent_consent_preference_is_not_accepted() -> None:
    account = verified_candidate("absent-consent@example.com")

    assert not has_current_consent(account)
    assert consent_status(account) is ConsentStatus.NOT_ACCEPTED
    assert not ConsentPreference.objects.filter(account=account).exists()


def test_first_ai_use_presents_the_approved_disclosure() -> None:
    client = Client()
    client.force_login(verified_candidate("disclosure@example.com"))

    response = client.get(reverse("ai_consent"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "AI data use" in content
    assert "bounded extracted text" in content
    assert "may include sensitive information" in content
    assert "zero data retention" in content
    assert "not used for training" in content
    assert "automated errors" in content
    assert "content-free audit" in content
    assert "withdraw" in content.lower()
    assert "OpenRouter" in content
    assert "privacy notice" in content


def test_accepting_current_policy_consent_permits_later_ai_entry_points() -> None:
    account = verified_candidate("accepting@example.com")
    client = Client()
    client.force_login(account)

    response = client.post(reverse("ai_consent"), {"accept": "1", "next": "/applications/"})

    assert response.status_code == 302
    assert response.headers["Location"] == "/applications/"
    assert has_current_consent(account)
    assert consent_status(account) is ConsentStatus.CURRENT
    preference = preference_for(account)
    assert preference.accepted is True
    assert preference.policy == current_policy()
    assert gated_probe(account).status_code == 200


def test_declining_consent_preserves_manual_workflows() -> None:
    account = verified_candidate("declining@example.com")
    Campaign.objects.create(
        account=account,
        weekly_target=5,
        monthly_target=20,
        timezone="Europe/London",
    )
    client = Client()
    client.force_login(account)

    response = client.post(reverse("ai_consent"), {"decline": "1", "next": "/"})

    assert response.status_code == 302
    assert not has_current_consent(account)
    assert consent_status(account) is ConsentStatus.NOT_ACCEPTED
    assert client.get(reverse("profile")).status_code == 200
    assert client.get(reverse("application_board")).status_code == 200
    assert client.get(reverse("application_create")).status_code == 200


def test_withdrawal_blocks_future_ai_entry_points_and_keeps_accepted_data() -> None:
    account = verified_candidate("withdrawal@example.com")
    client = Client()
    client.force_login(account)
    client.post(reverse("ai_consent"), {"accept": "1"})
    assert has_current_consent(account)

    response = client.post(reverse("ai_consent"), {"withdraw": "1"})

    assert response.status_code == 302
    assert not has_current_consent(account)
    assert consent_status(account) is ConsentStatus.NOT_ACCEPTED
    assert gated_probe(account).status_code == 302
    assert gated_probe(account).headers["Location"].startswith(reverse("ai_consent"))
    assert client.get(reverse("profile")).status_code == 200


def test_account_controls_show_consent_and_allow_withdrawal() -> None:
    account = verified_candidate("controls@example.com")
    client = Client()
    client.force_login(account)
    client.post(reverse("ai_consent"), {"accept": "1"})

    controls = client.get(reverse("account_home"))

    assert controls.status_code == 200
    content = controls.content.decode()
    assert "AI data use consent" in content
    assert "accepted" in content.lower()
    assert "withdraw" in content.lower()
    assert "See your AI data" in content

    withdrawal = client.post(
        reverse("ai_consent"),
        {"withdraw": "1", "next": reverse("account_home")},
    )

    assert withdrawal.status_code == 302
    assert withdrawal.headers["Location"] == reverse("account_home")
    assert not has_current_consent(account)
    updated = client.get(reverse("account_home")).content.decode()
    assert "not accepted" in updated.lower()


def test_changing_consent_policy_invalidates_earlier_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = verified_candidate("renewal@example.com")
    client = Client()
    client.force_login(account)
    client.post(reverse("ai_consent"), {"accept": "1"})
    assert has_current_consent(account)
    monkeypatch.setattr(settings, "AI_CONSENT", {"POLICY": "2030-renewed-policy"})

    assert not has_current_consent(account)
    assert consent_status(account) is ConsentStatus.RENEWAL_REQUIRED
    assert gated_probe(account).status_code == 302

    renewed = client.post(
        reverse("ai_consent"),
        {"accept": "1", "next": reverse("dashboard")},
    )

    assert renewed.status_code == 302
    assert has_current_consent(account)
    assert preference_for(account).policy == "2030-renewed-policy"


def test_accept_redirects_reject_unsafe_next_values() -> None:
    account = verified_candidate("unsafe-next@example.com")
    client = Client()
    client.force_login(account)

    response = client.post(
        reverse("ai_consent"),
        {"accept": "1", "next": "https://evil.example/phish"},
    )

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("dashboard")


def test_consent_requires_verified_authentication() -> None:
    anonymous = Client()

    assert anonymous.get(reverse("ai_consent")).status_code == 302

    account = unverified_candidate("unverified-consent@example.com")
    unverified = Client()
    unverified.force_login(account)

    response = unverified.get(reverse("ai_consent"))

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("account_login")


def test_consent_state_is_isolated_per_account() -> None:
    accepting = verified_candidate("isolated-a@example.com")
    other = verified_candidate("isolated-b@example.com")

    client = Client()
    client.force_login(accepting)
    client.post(reverse("ai_consent"), {"accept": "1"})

    assert has_current_consent(accepting)
    assert not has_current_consent(other)
    other_client = Client()
    other_client.force_login(other)
    assert "not accepted" in other_client.get(reverse("account_home")).content.decode().lower()


def test_account_deletion_removes_the_consent_preference() -> None:
    account = verified_candidate("delete-consent@example.com")
    client = Client()
    client.force_login(account)
    client.post(reverse("ai_consent"), {"accept": "1"})
    assert ConsentPreference.objects.filter(account=account).exists()

    delete_account(account=account)

    assert not ConsentPreference.objects.filter(account=account).exists()
    assert not Account.objects.filter(pk=account.pk).exists()
