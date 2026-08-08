import pytest
from allauth.account.models import EmailAddress
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Account
from apps.applications.models import JobApplication
from apps.applications.services import create_or_reuse_company
from apps.campaigns.models import Campaign
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


@pytest.mark.django_db
def test_company_website_is_normalized_to_its_registrable_idna_domain() -> None:
    company, created = create_or_reuse_company(
        name="Example Careers",
        website="https://jobs.bücher.example.co.uk/openings/42",
    )

    assert created is True
    assert company.name == "Example Careers"
    assert company.canonical_domain == "example.co.uk"


@pytest.mark.django_db
def test_duplicate_canonical_and_alias_domains_reuse_the_existing_company() -> None:
    company, _ = create_or_reuse_company("Example", "example.com")
    company.domain_aliases.create(domain="example.org")

    canonical_match, canonical_created = create_or_reuse_company(
        "Different name", "jobs.example.com"
    )
    alias_match, alias_created = create_or_reuse_company("Another name", "example.org")

    assert canonical_created is False
    assert canonical_match == company
    assert alias_created is False
    assert alias_match == company


@pytest.mark.django_db
def test_name_only_company_is_provisional() -> None:
    company, created = create_or_reuse_company("Stealth Startup")

    assert created is True
    assert company.name == "Stealth Startup"
    assert company.canonical_domain is None


@pytest.mark.django_db
def test_candidate_can_create_repeat_and_edit_draft_applications_with_htmx_parity() -> None:
    account = verified_candidate("candidate@example.com")
    client = Client()
    client.force_login(account)

    created = client.post(
        reverse("application_create"),
        {
            "company_name": "Example Careers",
            "website": "jobs.example.com",
            "role_title": "Platform engineer",
            "job_description": "Build dependable internal systems.",
            "posting_url": "https://jobs.example.com/42",
            "location": "Remote",
            "compensation": "100000 GBP",
            "source": "Referral",
            "private_notes": "Ask Ada for an introduction.",
        },
    )
    first = JobApplication.objects.get()
    repeated = client.post(
        reverse("application_create"),
        {
            "company": str(first.company_id),
            "role_title": "Platform engineer",
            "job_description": "A second application for the same role.",
        },
        headers={"HX-Request": "true"},
    )
    edited = client.post(
        reverse("application_edit", args=[first.pk]),
        {
            "role_title": "Senior platform engineer",
            "job_description": "Build dependable internal systems.",
            "posting_url": "",
            "location": "Hybrid",
            "compensation": "",
            "source": "",
            "private_notes": "Updated private note.",
        },
    )

    assert created.status_code == 302
    assert created.headers["Location"] == reverse("application_edit", args=[first.pk])
    assert first.campaign.account == account
    assert first.stage == JobApplication.Stage.DRAFT
    assert first.company.canonical_domain == "example.com"
    assert repeated.status_code == 200
    assert repeated.headers["HX-Redirect"].endswith("/edit/")
    assert JobApplication.objects.filter(company=first.company).count() == 2
    assert edited.status_code == 302
    first.refresh_from_db()
    assert first.role_title == "Senior platform engineer"
    assert first.location == "Hybrid"
    assert first.private_notes == "Updated private note."


@pytest.mark.django_db
def test_draft_validation_and_edits_are_scoped_to_the_authenticated_candidate() -> None:
    owner = verified_candidate("owner@example.com")
    intruder = verified_candidate("intruder@example.com")
    company, _ = create_or_reuse_company("Example", "example.com")
    application = JobApplication.objects.create(
        account=owner,
        campaign=Campaign.objects.get(account=owner),
        company=company,
        role_title="Owner role",
        job_description="Private job description.",
    )
    client = Client()
    client.force_login(intruder)

    invalid = client.post(
        reverse("application_create"),
        {"company": str(company.pk), "role_title": "", "job_description": ""},
    )
    edit = client.post(
        reverse("application_edit", args=[application.pk]),
        {"role_title": "Changed", "job_description": "Changed"},
    )

    assert invalid.status_code == 200
    assert b"This field is required." in invalid.content
    assert edit.status_code == 404
    application.refresh_from_db()
    assert application.role_title == "Owner role"


@pytest.mark.django_db
def test_candidate_can_search_shared_companies_by_public_identity() -> None:
    account = verified_candidate("candidate@example.com")
    create_or_reuse_company("Example Careers", "jobs.example.com")
    other, _ = create_or_reuse_company("Other Company", "other.example.org")
    other.domain_aliases.create(domain="other.co.uk")
    client = Client()
    client.force_login(account)

    response = client.get(reverse("application_create"), {"q": "other.co.uk"})

    assert response.status_code == 200
    assert b"Other Company" in response.content
    assert b"Example Careers" not in response.content
