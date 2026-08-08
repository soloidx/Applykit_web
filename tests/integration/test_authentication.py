import re

import pytest
from allauth.account.models import EmailAddress
from django.core import mail
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Account

pytestmark = pytest.mark.integration


def signup(client: Client, email: str) -> object:
    return client.post(
        reverse("account_signup"),
        {"email": email, "password1": "a-secure-password", "password2": "a-secure-password"},
    )


@pytest.mark.django_db
def test_registration_creates_unverified_account_and_sends_verification_email() -> None:
    response = signup(Client(), "candidate@example.com")

    assert response.status_code == 302
    assert response.url == reverse("account_email_verification_sent")
    assert Account.objects.filter(email="candidate@example.com").count() == 1
    assert EmailAddress.objects.get(email="candidate@example.com").verified is False
    assert len(mail.outbox) == 1
    assert "Confirm" in mail.outbox[0].subject


@pytest.mark.django_db
def test_email_confirmation_verifies_the_account_before_sign_in() -> None:
    client = Client()
    signup(client, "verification@example.com")
    confirmation_link = re.search(r"https?://\S+", mail.outbox[0].body).group(0)

    confirmation_page = client.get(confirmation_link)
    response = client.post(confirmation_link)

    assert confirmation_page.status_code == 200
    assert b"Confirm Email Address" in confirmation_page.content
    assert response.status_code == 302
    assert EmailAddress.objects.get(email="verification@example.com").verified is True


@pytest.mark.django_db
def test_duplicate_registration_is_rejected_without_creating_another_account() -> None:
    client = Client()
    signup(client, "candidate@example.com")

    response = signup(client, "CANDIDATE@example.com")

    assert response.status_code == 200
    assert b"registered" in response.content
    assert Account.objects.count() == 1


@pytest.mark.django_db
def test_verified_account_can_sign_in_and_reach_private_dashboard() -> None:
    account = Account.objects.create_user("candidate@example.com", "a-secure-password")
    EmailAddress.objects.create(user=account, email=account.email, primary=True, verified=True)
    client = Client()

    response = client.post(
        reverse("account_login"),
        {"login": account.email, "password": "a-secure-password"},
    )

    assert response.status_code == 302
    assert response.url == reverse("home")
    dashboard = client.get(reverse("dashboard"))
    assert dashboard.status_code == 200
    assert b"Your private workspace" in dashboard.content
    assert account.email.encode() in dashboard.content


@pytest.mark.django_db
def test_unverified_account_cannot_sign_in() -> None:
    account = Account.objects.create_user("candidate@example.com", "a-secure-password")
    EmailAddress.objects.create(user=account, email=account.email, primary=True, verified=False)
    client = Client()

    client.post(
        reverse("account_login"),
        {"login": account.email, "password": "a-secure-password"},
    )

    response = client.get(reverse("dashboard"))
    assert response.status_code == 302
    assert response.url == f"{reverse('account_login')}?next={reverse('dashboard')}"


@pytest.mark.django_db
def test_private_dashboard_redirects_anonymous_users_to_sign_in() -> None:
    response = Client().get(reverse("dashboard"))

    assert response.status_code == 302
    assert response.url == f"{reverse('account_login')}?next={reverse('dashboard')}"


@pytest.mark.django_db
def test_sign_out_ends_access_to_private_dashboard() -> None:
    account = Account.objects.create_user("candidate@example.com", "a-secure-password")
    EmailAddress.objects.create(user=account, email=account.email, primary=True, verified=True)
    client = Client()
    client.login(username=account.email, password="a-secure-password")

    response = client.post(reverse("account_logout"))

    assert response.status_code == 302
    assert response.url == reverse("home")
    assert client.get(reverse("dashboard")).status_code == 302


@pytest.mark.django_db
def test_dashboard_only_shows_the_authenticated_account() -> None:
    first = Account.objects.create_user("first@example.com", "a-secure-password")
    second = Account.objects.create_user("second@example.com", "a-secure-password")
    EmailAddress.objects.create(user=first, email=first.email, primary=True, verified=True)
    EmailAddress.objects.create(user=second, email=second.email, primary=True, verified=True)
    client = Client()
    client.login(username=first.email, password="a-secure-password")

    response = client.get(reverse("dashboard"))

    assert response.status_code == 200
    assert b"first@example.com" in response.content
    assert b"second@example.com" not in response.content
