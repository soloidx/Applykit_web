import pytest
from allauth.account.models import EmailAddress
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Account
from apps.applications.models import Company, CompanyDomainAlias, JobApplication
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


@pytest.mark.django_db
def test_administrator_can_correct_a_company_canonical_domain() -> None:
    administrator = Account.objects.create_superuser("admin@example.com", "a-secure-password")
    company, _ = create_or_reuse_company("Example", "old.example.com")
    client = Client()
    client.force_login(administrator)

    response = client.post(
        reverse("admin:applications_company_change", args=[company.pk]),
        {"name": "Example Inc.", "canonical_domain": "https://jobs.example.com/openings"},
    )

    assert response.status_code == 302
    company.refresh_from_db()
    assert company.name == "Example Inc."
    assert company.canonical_domain == "example.com"


@pytest.mark.django_db
def test_administrator_cannot_correct_a_company_to_an_alias_domain() -> None:
    administrator = Account.objects.create_superuser("admin@example.com", "a-secure-password")
    company, _ = create_or_reuse_company("Example", "example.com")
    other, _ = create_or_reuse_company("Other", "other.example.org")
    other.domain_aliases.create(domain="example.org")
    client = Client()
    client.force_login(administrator)

    response = client.post(
        reverse("admin:applications_company_change", args=[company.pk]),
        {"name": "Example", "canonical_domain": "alias.example.org"},
    )

    assert response.status_code == 200
    assert b"already belongs to another company" in response.content
    company.refresh_from_db()
    assert company.canonical_domain == "example.com"


@pytest.mark.django_db
def test_administrator_cannot_correct_a_company_to_another_canonical_domain() -> None:
    administrator = Account.objects.create_superuser("admin@example.com", "a-secure-password")
    company, _ = create_or_reuse_company("Example", "example.com")
    create_or_reuse_company("Other", "other.example.org")
    client = Client()
    client.force_login(administrator)

    response = client.post(
        reverse("admin:applications_company_change", args=[company.pk]),
        {"name": "Example", "canonical_domain": "other.example.org"},
    )

    assert response.status_code == 200
    assert b"Company with this Canonical domain already exists." in response.content
    company.refresh_from_db()
    assert company.canonical_domain == "example.com"


@pytest.mark.django_db
def test_administrator_can_merge_company_and_preserve_application_references() -> None:
    administrator = Account.objects.create_superuser("admin@example.com", "a-secure-password")
    candidate = verified_candidate("candidate@example.com")
    survivor, _ = create_or_reuse_company("Survivor", "survivor.example.com")
    duplicate, _ = create_or_reuse_company("Duplicate", "duplicate.example.net")
    duplicate.domain_aliases.create(domain="legacy.net")
    application = JobApplication.objects.create(
        account=candidate,
        campaign=Campaign.objects.get(account=candidate),
        company=duplicate,
        role_title="Platform engineer",
        job_description="Build dependable internal systems.",
        private_notes="Private candidate note.",
    )
    client = Client()
    client.force_login(administrator)

    response = client.post(
        reverse("admin:applications_company_change", args=[duplicate.pk]),
        {
            "name": "Duplicate",
            "canonical_domain": "duplicate.example.net",
            "merge_into": str(survivor.pk),
        },
    )

    assert response.status_code == 302
    assert not Company.objects.filter(pk=duplicate.pk).exists()
    application.refresh_from_db()
    assert application.company == survivor
    assert application.private_notes == "Private candidate note."
    assert set(
        CompanyDomainAlias.objects.filter(company=survivor).values_list("domain", flat=True)
    ) == {
        "example.net",
        "legacy.net",
    }
    recreated, created = create_or_reuse_company("Duplicate", "duplicate.example.net")
    assert created is False
    assert recreated == survivor
    alias_match, alias_created = create_or_reuse_company("Legacy", "legacy.net")
    assert alias_created is False
    assert alias_match == survivor


@pytest.mark.django_db
def test_administrator_can_merge_a_provisional_company() -> None:
    administrator = Account.objects.create_superuser("admin@example.com", "a-secure-password")
    candidate = verified_candidate("candidate@example.com")
    survivor, _ = create_or_reuse_company("Survivor", "survivor.example.com")
    duplicate, _ = create_or_reuse_company("Provisional duplicate")
    application = JobApplication.objects.create(
        account=candidate,
        campaign=Campaign.objects.get(account=candidate),
        company=duplicate,
        role_title="Platform engineer",
        job_description="Build dependable internal systems.",
    )
    client = Client()
    client.force_login(administrator)

    response = client.post(
        reverse("admin:applications_company_change", args=[duplicate.pk]),
        {"name": "Provisional duplicate", "canonical_domain": "", "merge_into": str(survivor.pk)},
    )

    assert response.status_code == 302
    assert not Company.objects.filter(pk=duplicate.pk).exists()
    application.refresh_from_db()
    assert application.company == survivor


@pytest.mark.django_db
def test_administrator_continues_at_surviving_company_after_merge() -> None:
    administrator = Account.objects.create_superuser("admin@example.com", "a-secure-password")
    survivor, _ = create_or_reuse_company("Survivor", "survivor.example.com")
    duplicate, _ = create_or_reuse_company("Duplicate", "duplicate.example.net")
    client = Client()
    client.force_login(administrator)

    response = client.post(
        reverse("admin:applications_company_change", args=[duplicate.pk]),
        {
            "name": "Duplicate",
            "canonical_domain": "duplicate.example.net",
            "merge_into": str(survivor.pk),
            "_continue": "Save and continue editing",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"] == reverse(
        "admin:applications_company_change", args=[survivor.pk]
    )


@pytest.mark.django_db
def test_failed_administrator_merge_rolls_back_application_reassignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    administrator = Account.objects.create_superuser("admin@example.com", "a-secure-password")
    candidate = verified_candidate("candidate@example.com")
    survivor, _ = create_or_reuse_company("Survivor", "survivor.example.com")
    duplicate, _ = create_or_reuse_company("Duplicate", "duplicate.example.net")
    application = JobApplication.objects.create(
        account=candidate,
        campaign=Campaign.objects.get(account=candidate),
        company=duplicate,
        role_title="Platform engineer",
        job_description="Build dependable internal systems.",
    )

    def fail_alias_creation(*args: object, **kwargs: object) -> CompanyDomainAlias:
        raise RuntimeError("Alias persistence failed")

    monkeypatch.setattr(CompanyDomainAlias.objects, "create", fail_alias_creation)
    client = Client(raise_request_exception=False)
    client.force_login(administrator)

    response = client.post(
        reverse("admin:applications_company_change", args=[duplicate.pk]),
        {
            "name": "Duplicate",
            "canonical_domain": "duplicate.example.net",
            "merge_into": str(survivor.pk),
        },
    )

    assert response.status_code == 500
    assert Company.objects.filter(pk=duplicate.pk).exists()
    application.refresh_from_db()
    assert application.company == duplicate


@pytest.mark.django_db
def test_candidate_cannot_access_company_administration() -> None:
    candidate = verified_candidate("candidate@example.com")
    company, _ = create_or_reuse_company("Example", "example.com")
    client = Client()
    client.force_login(candidate)

    response = client.post(
        reverse("admin:applications_company_change", args=[company.pk]),
        {"name": "Changed", "canonical_domain": "changed.example.org"},
    )

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/admin/login/")
    company.refresh_from_db()
    assert company.name == "Example"
    assert company.canonical_domain == "example.com"


@pytest.mark.django_db
def test_candidate_cannot_change_a_company_identity_through_draft_edit_fields() -> None:
    candidate = verified_candidate("candidate@example.com")
    company, _ = create_or_reuse_company("Example", "example.com")
    application = JobApplication.objects.create(
        account=candidate,
        campaign=Campaign.objects.get(account=candidate),
        company=company,
        role_title="Platform engineer",
        job_description="Build dependable internal systems.",
    )
    client = Client()
    client.force_login(candidate)

    response = client.post(
        reverse("application_edit", args=[application.pk]),
        {
            "role_title": "Platform engineer",
            "job_description": "Build dependable internal systems.",
            "company": "999",
            "canonical_domain": "changed.example.org",
        },
    )

    assert response.status_code == 302
    application.refresh_from_db()
    company.refresh_from_db()
    assert application.company == company
    assert company.canonical_domain == "example.com"
