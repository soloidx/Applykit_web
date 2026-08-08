import pytest
from allauth.account.models import EmailAddress
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Account
from apps.applications.models import Company, JobApplication
from apps.applications.services import transition_application
from apps.campaigns.models import Campaign
from apps.profiles.models import CandidateProfile

pytestmark = pytest.mark.integration


def verified_candidate(email: str, timezone: str = "Europe/London") -> Account:
    account = Account.objects.create_user(email, "a-secure-password")
    EmailAddress.objects.create(user=account, email=account.email, primary=True, verified=True)
    CandidateProfile.objects.create(account=account, full_name="Ada Lovelace", timezone=timezone)
    return account


@pytest.mark.django_db
def test_candidate_can_create_and_archive_an_active_campaign() -> None:
    account = verified_candidate("candidate@example.com")
    client = Client()
    client.force_login(account)

    created = client.post(
        reverse("campaign_create"),
        {"weekly_target": "5", "monthly_target": "20"},
    )

    campaign = Campaign.objects.get(account=account)
    dashboard = client.get(reverse("dashboard"))
    archived = client.post(reverse("campaign_archive", args=[campaign.pk]))
    replacement = client.post(
        reverse("campaign_create"),
        {"weekly_target": "3", "monthly_target": "12"},
    )

    assert created.status_code == 302
    assert campaign.status == Campaign.Status.ACTIVE
    assert campaign.timezone == "Europe/London"
    assert b"5 per week" in dashboard.content
    assert b"20 per month" in dashboard.content
    assert b"0 / 5" in dashboard.content
    assert b"0 / 20" in dashboard.content
    assert archived.status_code == 302
    campaign.refresh_from_db()
    assert campaign.status == Campaign.Status.ARCHIVED
    assert replacement.status_code == 302
    assert Campaign.objects.filter(account=account, status=Campaign.Status.ACTIVE).count() == 1


@pytest.mark.django_db
def test_campaign_creation_validates_targets_and_htmx_has_same_outcome() -> None:
    account = verified_candidate("candidate@example.com")
    client = Client()
    client.force_login(account)

    invalid = client.post(
        reverse("campaign_create"), {"weekly_target": "0", "monthly_target": "-1"}
    )
    valid = client.post(
        reverse("campaign_create"),
        {"weekly_target": "2", "monthly_target": "8"},
        headers={"HX-Request": "true"},
    )
    duplicate = client.post(
        reverse("campaign_create"), {"weekly_target": "3", "monthly_target": "12"}
    )
    campaign = Campaign.objects.get(account=account)
    archived = client.post(
        reverse("campaign_archive", args=[campaign.pk]), headers={"HX-Request": "true"}
    )

    assert invalid.status_code == 200
    assert b"greater than or equal to 1" in invalid.content
    assert valid.status_code == 200
    assert valid.headers["HX-Redirect"] == reverse("dashboard")
    assert duplicate.status_code == 200
    assert b"already have an active campaign" in duplicate.content
    assert archived.status_code == 200
    assert archived.headers["HX-Redirect"] == reverse("dashboard")
    campaign.refresh_from_db()
    assert campaign.status == Campaign.Status.ARCHIVED
    assert Campaign.objects.filter(
        account=account,
        weekly_target=2,
        monthly_target=8,
        timezone="Europe/London",
        status=Campaign.Status.ARCHIVED,
    ).exists()


@pytest.mark.django_db
def test_campaign_operations_are_scoped_to_the_authenticated_account() -> None:
    owner = verified_candidate("owner@example.com")
    intruder = verified_candidate("intruder@example.com")
    campaign = Campaign.objects.create(
        account=owner,
        weekly_target=99,
        monthly_target=20,
        timezone="UTC",
    )
    client = Client()
    client.force_login(intruder)

    response = client.post(reverse("campaign_archive", args=[campaign.pk]))
    dashboard = client.get(reverse("dashboard"))

    assert response.status_code == 404
    assert b"99 per week" not in dashboard.content
    campaign.refresh_from_db()
    assert campaign.status == Campaign.Status.ACTIVE


@pytest.mark.django_db
def test_campaign_constraints_allow_only_one_active_campaign_and_keep_timezone_snapshot() -> None:
    account = verified_candidate("candidate@example.com")
    first = Campaign.objects.create(
        account=account,
        weekly_target=5,
        monthly_target=20,
        timezone=account.candidate_profile.timezone,
    )
    account.candidate_profile.timezone = "America/New_York"
    account.candidate_profile.save(update_fields=["timezone"])

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Campaign.objects.create(
                account=account,
                weekly_target=3,
                monthly_target=12,
                timezone="America/New_York",
            )

    first.refresh_from_db()
    assert first.timezone == "Europe/London"


@pytest.mark.django_db
def test_dashboard_progress_counts_submissions_beyond_submitted_but_not_drafts() -> None:
    account = verified_candidate("progress@example.com")
    campaign = Campaign.objects.create(
        account=account,
        weekly_target=5,
        monthly_target=20,
        timezone="Europe/London",
    )
    company = Company.objects.create(name="Example")
    submitted = JobApplication.objects.create(
        account=account,
        campaign=campaign,
        company=company,
        role_title="Submitted role",
        job_description="Description",
    )
    JobApplication.objects.create(
        account=account,
        campaign=campaign,
        company=company,
        role_title="Draft role",
        job_description="Description",
    )
    transition_application(
        account=account,
        application_id=submitted.pk,
        stage=JobApplication.Stage.SUBMITTED,
    )
    transition_application(
        account=account,
        application_id=submitted.pk,
        stage=JobApplication.Stage.ACCEPTED,
    )
    client = Client()
    client.force_login(account)

    dashboard = client.get(reverse("dashboard"))

    assert dashboard.status_code == 200
    assert b"1 / 5" in dashboard.content
    assert b"1 / 20" in dashboard.content
