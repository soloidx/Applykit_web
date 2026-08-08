import pytest
from allauth.account.models import EmailAddress
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Account
from apps.profiles.models import CandidateProfile

pytestmark = pytest.mark.integration


def verified_account(email: str) -> Account:
    account = Account.objects.create_user(email, "a-secure-password")
    EmailAddress.objects.create(user=account, email=account.email, primary=True, verified=True)
    return account


def profile_data(**overrides: str) -> dict[str, str]:
    data = {
        "full_name": "Ada Lovelace",
        "timezone": "Europe/London",
        "professional_title": "Analytical engine specialist",
        "professional_summary": "I make complex systems easier to understand.",
        "phone_number": "+44 20 7946 0958",
        "location": "London, UK",
        "linkedin_url": "https://www.linkedin.com/in/ada-lovelace",
        "portfolio_url": "https://ada.example.com",
    }
    data.update(overrides)
    return data


@pytest.mark.django_db
def test_anonymous_user_is_sent_to_sign_in_from_profile_onboarding() -> None:
    response = Client().get(reverse("profile"))

    assert response.status_code == 302
    assert response.url == f"{reverse('account_login')}?next={reverse('profile')}"


@pytest.mark.django_db
def test_verified_account_is_sent_to_profile_before_private_dashboard() -> None:
    account = verified_account("candidate@example.com")
    client = Client()
    client.force_login(account)

    response = client.get(reverse("dashboard"))

    assert response.status_code == 302
    assert response.url == reverse("profile")
    assert CandidateProfile.objects.filter(account=account).exists() is False


@pytest.mark.django_db
def test_profile_requires_full_name_and_valid_iana_timezone() -> None:
    account = verified_account("candidate@example.com")
    client = Client()
    client.force_login(account)

    response = client.post(
        reverse("profile"),
        {"full_name": "", "timezone": "not/a-timezone"},
    )

    assert response.status_code == 200
    assert b"This field is required." in response.content
    assert b"Enter a valid IANA timezone" in response.content
    assert CandidateProfile.objects.filter(account=account).exists() is False


@pytest.mark.django_db
def test_minimum_profile_completion_unlocks_dashboard() -> None:
    account = verified_account("candidate@example.com")
    client = Client()
    client.force_login(account)

    response = client.post(reverse("profile"), profile_data())

    assert response.status_code == 302
    assert response.url == reverse("dashboard")
    profile = CandidateProfile.objects.get(account=account)
    assert profile.full_name == "Ada Lovelace"
    assert profile.timezone == "Europe/London"
    assert client.get(reverse("dashboard")).status_code == 200


@pytest.mark.django_db
def test_optional_profile_details_can_be_added_progressively() -> None:
    account = verified_account("candidate@example.com")
    client = Client()
    client.force_login(account)
    client.post(reverse("profile"), profile_data())

    response = client.post(
        reverse("profile"),
        profile_data(
            professional_title="Principal systems designer",
            professional_summary="A refreshed summary.",
            phone_number="",
            location="Cambridge, UK",
            linkedin_url="",
            portfolio_url="",
        ),
    )

    assert response.status_code == 302
    profile = CandidateProfile.objects.get(account=account)
    assert profile.professional_title == "Principal systems designer"
    assert profile.professional_summary == "A refreshed summary."
    assert profile.phone_number == ""
    assert profile.location == "Cambridge, UK"


@pytest.mark.django_db
def test_htmx_profile_submission_has_the_same_persisted_result_and_validation_errors() -> None:
    account = verified_account("candidate@example.com")
    client = Client()
    client.force_login(account)

    invalid_response = client.post(
        reverse("profile"),
        {"full_name": "Ada Lovelace", "timezone": "Mars/Colony"},
        headers={"HX-Request": "true"},
    )
    valid_response = client.post(
        reverse("profile"),
        profile_data(),
        headers={"HX-Request": "true"},
    )

    assert invalid_response.status_code == 200
    assert b"Enter a valid IANA timezone" in invalid_response.content
    assert valid_response.status_code == 200
    assert b"Profile saved" in valid_response.content
    assert CandidateProfile.objects.filter(
        account=account,
        full_name="Ada Lovelace",
        timezone="Europe/London",
    ).exists()


@pytest.mark.django_db
def test_account_can_own_at_most_one_candidate_profile() -> None:
    account = verified_account("candidate@example.com")
    CandidateProfile.objects.create(account=account, full_name="Ada Lovelace", timezone="UTC")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CandidateProfile.objects.create(
                account=account, full_name="Another Name", timezone="UTC"
            )


@pytest.mark.django_db
def test_profile_reads_and_writes_are_isolated_by_authenticated_account() -> None:
    first = verified_account("first@example.com")
    second = verified_account("second@example.com")
    CandidateProfile.objects.create(account=first, full_name="First Candidate", timezone="UTC")
    CandidateProfile.objects.create(account=second, full_name="Second Candidate", timezone="UTC")
    client = Client()
    client.force_login(first)

    response = client.get(reverse("profile"))
    client.post(reverse("profile"), profile_data(full_name="Updated First Candidate"))

    assert response.status_code == 200
    assert b"First Candidate" in response.content
    assert b"Second Candidate" not in response.content
    assert CandidateProfile.objects.get(account=second).full_name == "Second Candidate"


@pytest.mark.django_db
def test_unverified_account_cannot_access_profile_onboarding() -> None:
    account = Account.objects.create_user("candidate@example.com", "a-secure-password")
    EmailAddress.objects.create(user=account, email=account.email, primary=True, verified=False)
    client = Client()
    client.force_login(account)

    response = client.get(reverse("profile"))

    assert response.status_code == 302
    assert response.url == reverse("account_login")
