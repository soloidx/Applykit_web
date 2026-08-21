import time
from pathlib import Path

import pytest
from allauth.account.models import EmailAddress
from django.apps import apps
from django.contrib.staticfiles import finders
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.db.migrations.loader import MigrationLoader
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import Account
from apps.applications.models import Company, JobApplication
from apps.campaigns.models import Campaign
from apps.cover_letters.models import CoverLetter
from apps.cover_letters.services import build_cover_letter_starter_template
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


@pytest.mark.django_db
def test_cover_letter_starter_template_is_deterministic_and_uses_current_context() -> None:
    account = verified_candidate("cover-letter-template@example.com")
    application = application_for(account, "Staff engineer")

    assert build_cover_letter_starter_template(
        application=application, profile=account.candidate_profile
    ) == (
        "<p>Dear Hiring Team,</p>"
        "<p>I am writing to apply for the Staff engineer position at Staff engineer Company.</p>"
        "<p>[Describe one or two relevant experiences, achievements, or skills "
        "that address this role.]</p>"
        "<p>[Explain why this role and Staff engineer Company interest you.]</p>"
        "<p>Thank you for your time and consideration.</p>"
        "<p>Sincerely,<br>Ada Lovelace</p>"
    )


@pytest.mark.django_db
def test_cover_letter_workbench_is_optional_until_first_successful_save() -> None:
    account = verified_candidate("cover-letter-workbench@example.com")
    application = application_for(account)
    client = Client()
    client.force_login(account)

    response = client.get(reverse("cover_letter_detail", args=[application.pk]))

    assert response.status_code == 200
    assert b"Not created" in response.content
    assert b"New blank letter" in response.content
    assert b"Load starter template" in response.content
    assert not CoverLetter.objects.exists()


@pytest.mark.django_db
def test_cover_letter_save_sanitizes_updates_and_supports_htmx_redirects() -> None:
    account = verified_candidate("cover-letter-save@example.com")
    application = application_for(account)
    client = Client()
    client.force_login(account)

    response = client.post(
        reverse("cover_letter_save", args=[application.pk]),
        {"body_html": "<p>Hello <strong>team</strong>.</p><script>alert(1)</script>"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == reverse("cover_letter_detail", args=[application.pk])
    assert CoverLetter.objects.get(application=application).body_html == (
        "<p>Hello <strong>team</strong>.</p>"
    )

    response = client.post(
        reverse("cover_letter_save", args=[application.pk]),
        {"body_html": "<p>Updated.</p>"},
    )

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("cover_letter_detail", args=[application.pk])
    assert CoverLetter.objects.get(application=application).body_html == "<p>Updated.</p>"


@pytest.mark.django_db
def test_blank_cover_letter_save_rejects_without_deleting_existing_letter() -> None:
    account = verified_candidate("cover-letter-blank@example.com")
    application = application_for(account)
    CoverLetter.objects.create(application=application, body_html="<p>Keep me.</p>")
    client = Client()
    client.force_login(account)

    response = client.post(
        reverse("cover_letter_save", args=[application.pk]),
        {"body_html": "<p><br></p><p> </p>"},
    )

    assert response.status_code == 200
    assert b"must contain visible text" in response.content
    assert CoverLetter.objects.get(application=application).body_html == "<p>Keep me.</p>"


@pytest.mark.django_db
def test_blank_first_save_does_not_create_a_cover_letter() -> None:
    account = verified_candidate("cover-letter-blank-first@example.com")
    application = application_for(account)
    client = Client()
    client.force_login(account)

    response = client.post(
        reverse("cover_letter_save", args=[application.pk]),
        {"body_html": "<p> </p>"},
    )

    assert response.status_code == 200
    assert not CoverLetter.objects.exists()


@pytest.mark.django_db
def test_failed_cover_letter_update_preserves_saved_content_for_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = verified_candidate("cover-letter-rollback@example.com")
    application = application_for(account)
    CoverLetter.objects.create(application=application, body_html="<p>Keep me.</p>")

    def fail_save(*args: object, **kwargs: object) -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(CoverLetter, "save", fail_save)
    client = Client()
    client.force_login(account)

    response = client.post(
        reverse("cover_letter_save", args=[application.pk]),
        {"body_html": "<p>Retry me.</p>"},
    )

    assert response.status_code == 200
    assert b"Save failed" in response.content
    assert b"Retry me." in response.content
    assert CoverLetter.objects.get(application=application).body_html == "<p>Keep me.</p>"


@pytest.mark.django_db
def test_cover_letter_delete_requires_confirmation_then_returns_to_not_created() -> None:
    account = verified_candidate("cover-letter-delete@example.com")
    application = application_for(account)
    CoverLetter.objects.create(application=application, body_html="<p>Delete me.</p>")
    client = Client()
    client.force_login(account)

    confirmation = client.post(reverse("cover_letter_delete", args=[application.pk]))

    assert confirmation.status_code == 200
    assert b"Delete this Cover Letter" in confirmation.content
    assert CoverLetter.objects.filter(application=application).exists()

    deleted = client.post(
        reverse("cover_letter_delete", args=[application.pk]),
        {"confirm": "1"},
    )

    assert deleted.status_code == 302
    assert deleted.headers["Location"] == reverse("cover_letter_detail", args=[application.pk])
    assert not CoverLetter.objects.filter(application=application).exists()


@pytest.mark.django_db
def test_cover_letter_delete_supports_htmx_redirect_parity() -> None:
    account = verified_candidate("cover-letter-delete-htmx@example.com")
    application = application_for(account)
    CoverLetter.objects.create(application=application, body_html="<p>Delete me.</p>")
    client = Client()
    client.force_login(account)

    response = client.post(
        reverse("cover_letter_delete", args=[application.pk]),
        {"confirm": "1"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == reverse("cover_letter_detail", args=[application.pk])
    assert not CoverLetter.objects.filter(application=application).exists()


@pytest.mark.django_db
def test_cover_letter_routes_are_account_scoped_and_post_only_for_mutations() -> None:
    owner = verified_candidate("cover-letter-route-owner@example.com")
    intruder = verified_candidate("cover-letter-route-intruder@example.com")
    application = application_for(owner)
    client = Client()
    client.force_login(intruder)

    assert client.get(reverse("cover_letter_detail", args=[application.pk])).status_code == 404
    assert (
        client.post(
            reverse("cover_letter_save", args=[application.pk]),
            {"body_html": "<p>x</p>"},
        ).status_code
        == 404
    )
    assert client.get(reverse("cover_letter_save", args=[application.pk])).status_code == 405
    assert client.get(reverse("cover_letter_delete", args=[application.pk])).status_code == 405


@pytest.mark.django_db
def test_quill_is_pinned_self_hosted_and_collected_into_asset_builds(tmp_path: Path) -> None:
    script_path = finders.find("vendor/quill/quill.min.js")
    stylesheet_path = finders.find("vendor/quill/quill.snow.css")

    assert script_path is not None
    assert stylesheet_path is not None

    script = Path(script_path)
    parent = script.parent
    assert Path(stylesheet_path).parent == parent
    pinned_version = (parent / "VERSION").read_text().strip()
    assert pinned_version == "2.0.3"
    assert (parent / "LICENSE.txt").exists()
    assert f'version="{pinned_version}"' in script.read_text()

    with override_settings(STATIC_ROOT=str(tmp_path)):
        call_command("collectstatic", interactive=False, verbosity=0)

    assert (tmp_path / "vendor/quill/quill.min.js").exists()
    assert (tmp_path / "vendor/quill/quill.snow.css").exists()
