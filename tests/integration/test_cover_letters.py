import time

import pytest
from allauth.account.models import EmailAddress
from django.apps import apps
from django.db import IntegrityError, connection, transaction
from django.db.migrations.loader import MigrationLoader

from apps.accounts.models import Account
from apps.applications.models import Company, JobApplication
from apps.campaigns.models import Campaign
from apps.cover_letters.models import CoverLetter
from apps.profiles.models import CandidateProfile

pytestmark = pytest.mark.integration


def verified_candidate(email: str) -> Account:
    account = Account.objects.create_user(email, "a-secure-password")
    EmailAddress.objects.create(user=account, email=account.email, primary=True, verified=True)
    CandidateProfile.objects.create(
        account=account,
        full_name="Ada Lovelace",
        timezone="Europe/London",
    )
    Campaign.objects.create(
        account=account,
        weekly_target=5,
        monthly_target=20,
        timezone="Europe/London",
    )
    return account


def application_for(account: Account, role_title: str = "Platform engineer") -> JobApplication:
    return JobApplication.objects.create(
        account=account,
        campaign=Campaign.objects.get(account=account),
        company=Company.objects.create(name=f"{role_title} Company"),
        role_title=role_title,
        job_description="Build dependable internal systems.",
    )


@pytest.mark.django_db
def test_cover_letters_app_is_registered_and_migration_follows_applications() -> None:
    assert apps.get_app_config("cover_letters").name == "apps.cover_letters"

    migration = MigrationLoader(connection=connection).get_migration(
        "cover_letters", "0001_initial"
    )

    applications_migration = (
        "applications",
        "0006_remove_applicationskillrequirement_application_skill_requirement_label_not_blank_and_more",
    )
    assert applications_migration in migration.dependencies


@pytest.mark.django_db
def test_cover_letter_is_optional_and_ownership_is_derived_from_application_account() -> None:
    owner = verified_candidate("cover-letter-owner@example.com")
    other = verified_candidate("cover-letter-other@example.com")
    application = application_for(owner)
    other_application = application_for(other, "Other engineer")

    assert CoverLetter.objects.count() == 0

    cover_letter = CoverLetter.objects.create(
        application=application,
        body_html="<p>Dear Hiring Team,<br><strong>I am excited to apply.</strong></p>",
    )

    assert cover_letter.application_id == application.pk
    assert cover_letter.application.account_id == owner.pk
    assert not hasattr(cover_letter, "account_id")
    assert list(CoverLetter.objects.filter(application__account=owner)) == [cover_letter]
    assert not CoverLetter.objects.filter(application__account=other).exists()
    assert not CoverLetter.objects.filter(application=other_application).exists()


@pytest.mark.django_db
def test_cover_letter_stores_canonical_html_and_timestamps() -> None:
    account = verified_candidate("cover-letter-fields@example.com")
    application = application_for(account)
    body_html = "<p>Dear Hiring Team,</p><p>Thank you for your consideration.</p>"

    cover_letter = CoverLetter.objects.create(application=application, body_html=body_html)

    assert cover_letter.body_html == body_html
    assert cover_letter.created_at is not None
    assert cover_letter.updated_at is not None
    assert cover_letter.created_at <= cover_letter.updated_at

    time.sleep(0.001)
    cover_letter.body_html = "<p>Updated letter.</p>"
    cover_letter.save(update_fields=["body_html", "updated_at"])
    cover_letter.refresh_from_db()

    assert cover_letter.updated_at >= cover_letter.created_at
    assert cover_letter.body_html == "<p>Updated letter.</p>"


@pytest.mark.django_db
def test_one_cover_letter_per_application_is_enforced() -> None:
    account = verified_candidate("cover-letter-unique@example.com")
    application = application_for(account)
    CoverLetter.objects.create(application=application, body_html="<p>First.</p>")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CoverLetter.objects.create(application=application, body_html="<p>Second.</p>")


@pytest.mark.django_db
def test_deleting_application_cascades_cover_letter() -> None:
    account = verified_candidate("cover-letter-cascade@example.com")
    application = application_for(account)
    cover_letter = CoverLetter.objects.create(application=application, body_html="<p>Letter.</p>")

    application.delete()

    assert not CoverLetter.objects.filter(pk=cover_letter.pk).exists()
