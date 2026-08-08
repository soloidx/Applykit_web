import pytest
from django.test import Client

pytestmark = pytest.mark.integration


def test_home_page_is_available() -> None:
    response = Client().get("/")

    assert response.status_code == 200
    assert b"Turn a scattered search" in response.content


def test_account_uses_email_as_unique_identity() -> None:
    from apps.accounts.models import Account

    assert Account.USERNAME_FIELD == "email"
    assert Account._meta.get_field("email").unique is True


@pytest.mark.django_db
def test_email_auth_pages_are_available() -> None:
    client = Client()

    assert client.get("/accounts/login/").status_code == 200
    assert client.get("/accounts/signup/").status_code == 200


@pytest.mark.django_db
def test_account_manager_creates_email_identity() -> None:
    from apps.accounts.models import Account

    account = Account.objects.create_user("Candidate@Example.com", "a-secure-password")

    assert account.email == "Candidate@example.com"
    assert account.check_password("a-secure-password")
