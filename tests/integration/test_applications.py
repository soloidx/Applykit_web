from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from allauth.account.models import EmailAddress
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Account
from apps.applications.models import (
    ApplicationSkillRequirement,
    Company,
    CompanyDomainAlias,
    JobApplication,
    RecruitmentEvent,
    StageTransition,
)
from apps.applications.services import create_or_reuse_company, transition_application
from apps.campaigns.models import Campaign
from apps.profiles.models import CandidateProfile
from apps.skills.models import SkillAlias, SkillConcept

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
def test_applications_board_shows_active_campaign_applications_in_all_stage_columns() -> None:
    account = verified_candidate("board-candidate@example.com")
    active_campaign = Campaign.objects.get(account=account, status=Campaign.Status.ACTIVE)
    archived_campaign = Campaign.objects.create(
        account=account,
        weekly_target=3,
        monthly_target=12,
        timezone="Europe/London",
        status=Campaign.Status.ARCHIVED,
    )
    company = Company.objects.create(name="Example Careers")
    older = JobApplication.objects.create(
        account=account,
        campaign=active_campaign,
        company=company,
        role_title="Older platform engineer",
        job_description="Build dependable systems.",
    )
    newer = JobApplication.objects.create(
        account=account,
        campaign=active_campaign,
        company=company,
        role_title="Newer platform engineer",
        job_description="Build dependable systems.",
    )
    JobApplication.objects.create(
        account=account,
        campaign=archived_campaign,
        company=company,
        role_title="Archived platform engineer",
        job_description="Build dependable systems.",
    )
    JobApplication.objects.filter(pk=older.pk).update(updated_at=timezone.now() - timedelta(days=1))
    JobApplication.objects.filter(pk=newer.pk).update(updated_at=timezone.now())
    client = Client()
    client.force_login(account)

    response = client.get(reverse("application_board"))
    content = response.content.decode()

    assert response.status_code == 200
    for stage in JobApplication.Stage:
        assert f'id="stage-{stage.value}-heading"' in content
        assert stage.label in content
    assert "Newer platform engineer" in content
    assert "Older platform engineer" in content
    assert "Archived platform engineer" not in content
    assert content.index("Newer platform engineer") < content.index("Older platform engineer")
    assert reverse("application_detail", args=[newer.pk]) in content
    assert 'href="/applications/"' in content
    assert content.count('aria-current="page"') == 2


@pytest.mark.django_db
def test_applications_board_shows_empty_columns_without_an_active_campaign() -> None:
    account = verified_candidate("empty-board@example.com")
    Campaign.objects.filter(account=account).update(status=Campaign.Status.ARCHIVED)
    client = Client()
    client.force_login(account)

    response = client.get(reverse("application_board"))

    assert response.status_code == 200
    assert response.content.count(b"No applications in this stage.") == len(JobApplication.Stage)
    assert b"Add draft application" not in response.content


@pytest.mark.django_db
def test_authenticated_navigation_marks_dashboard_and_profile_sections() -> None:
    account = verified_candidate("navigation@example.com")
    client = Client()
    client.force_login(account)

    dashboard = client.get(reverse("dashboard"))
    profile = client.get(reverse("profile"))
    nested_profile = client.get(reverse("experience_create"))

    assert dashboard.content.count(b'aria-current="page"') == 2
    assert profile.content.count(b'aria-current="page"') == 2
    assert nested_profile.content.count(b'aria-current="page"') == 2
    assert b'href="/dashboard/"' in dashboard.content
    assert b'href="/profile/"' in profile.content
    assert b'href="/applications/"' in dashboard.content
    assert b"Sign out" in dashboard.content


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


@pytest.mark.django_db
def test_candidate_can_create_edit_and_complete_recruitment_events_with_htmx_parity() -> None:
    account = verified_candidate("event-candidate@example.com")
    company, _ = create_or_reuse_company("Example", "example.com")
    application = JobApplication.objects.create(
        account=account,
        campaign=Campaign.objects.get(account=account),
        company=company,
        role_title="Platform engineer",
        job_description="Build dependable internal systems.",
    )
    client = Client()
    client.force_login(account)

    created = client.post(
        reverse("recruitment_event_create", args=[application.pk]),
        {
            "event_type": RecruitmentEvent.EventType.INTERVIEW,
            "custom_title": "",
            "scheduled_at": "2030-05-01T15:30",
        },
    )
    event = RecruitmentEvent.objects.get()
    edited = client.post(
        reverse("recruitment_event_edit", args=[application.pk, event.pk]),
        {
            "event_type": RecruitmentEvent.EventType.DEADLINE,
            "custom_title": "",
            "scheduled_at": "2030-05-02T16:45",
            "status": RecruitmentEvent.Status.COMPLETED,
        },
        headers={"HX-Request": "true"},
    )
    detail = client.get(reverse("application_detail", args=[application.pk]))

    assert created.status_code == 302
    assert event.event_type == RecruitmentEvent.EventType.INTERVIEW
    assert event.status == RecruitmentEvent.Status.SCHEDULED
    assert event.scheduled_at.astimezone(ZoneInfo("Europe/London")).hour == 15
    assert edited.status_code == 200
    assert edited.headers["HX-Redirect"] == reverse("application_detail", args=[application.pk])
    event.refresh_from_db()
    assert event.event_type == RecruitmentEvent.EventType.DEADLINE
    assert event.status == RecruitmentEvent.Status.COMPLETED
    assert detail.status_code == 200
    assert b"Deadline" in detail.content
    assert b"Completed" in detail.content

    cancelled = client.post(
        reverse("recruitment_event_create", args=[application.pk]),
        {
            "event_type": RecruitmentEvent.EventType.FOLLOW_UP,
            "custom_title": "",
            "scheduled_at": "2030-05-03T16:45",
        },
    )
    cancelled_event = RecruitmentEvent.objects.exclude(pk=event.pk).get()
    cancellation = client.post(
        reverse(
            "recruitment_event_edit",
            args=[application.pk, cancelled_event.pk],
        ),
        {
            "event_type": RecruitmentEvent.EventType.FOLLOW_UP,
            "custom_title": "",
            "scheduled_at": "2030-05-03T16:45",
            "status": RecruitmentEvent.Status.CANCELLED,
        },
    )

    assert cancelled.status_code == 302
    assert cancellation.status_code == 302
    assert b"Cancelled" in client.get(reverse("application_detail", args=[application.pk])).content


@pytest.mark.django_db
def test_custom_recruitment_event_requires_and_displays_its_custom_title() -> None:
    account = verified_candidate("custom-event@example.com")
    company, _ = create_or_reuse_company("Example", "example.com")
    application = JobApplication.objects.create(
        account=account,
        campaign=Campaign.objects.get(account=account),
        company=company,
        role_title="Platform engineer",
        job_description="Build dependable internal systems.",
    )
    client = Client()
    client.force_login(account)

    invalid = client.post(
        reverse("recruitment_event_create", args=[application.pk]),
        {
            "event_type": RecruitmentEvent.EventType.CUSTOM,
            "custom_title": "",
            "scheduled_at": "2030-05-01T15:30",
        },
    )
    valid = client.post(
        reverse("recruitment_event_create", args=[application.pk]),
        {
            "event_type": RecruitmentEvent.EventType.CUSTOM,
            "custom_title": "Send portfolio follow-up",
            "scheduled_at": "2030-05-01T15:30",
        },
    )
    event = RecruitmentEvent.objects.get()
    invalid_edit = client.post(
        reverse("recruitment_event_edit", args=[application.pk, event.pk]),
        {
            "event_type": RecruitmentEvent.EventType.CUSTOM,
            "custom_title": "",
            "scheduled_at": "2030-05-01T15:30",
            "status": RecruitmentEvent.Status.SCHEDULED,
        },
    )

    assert invalid.status_code == 200
    assert b"Enter a title for a custom event." in invalid.content
    assert valid.status_code == 302
    assert invalid_edit.status_code == 200
    assert b"Enter a title for a custom event." in invalid_edit.content
    assert event.custom_title == "Send portfolio follow-up"
    assert (
        b"Send portfolio follow-up"
        in client.get(reverse("application_detail", args=[application.pk])).content
    )


@pytest.mark.django_db
def test_dashboard_shows_upcoming_events_in_candidate_time_and_order() -> None:
    account = verified_candidate("dashboard-events@example.com")
    account.candidate_profile.timezone = "America/New_York"
    account.candidate_profile.save(update_fields=["timezone"])
    company, _ = create_or_reuse_company("Example", "example.com")
    application = JobApplication.objects.create(
        account=account,
        campaign=Campaign.objects.get(account=account),
        company=company,
        role_title="Platform engineer",
        job_description="Build dependable internal systems.",
    )
    RecruitmentEvent.objects.create(
        application=application,
        event_type=RecruitmentEvent.EventType.INTERVIEW,
        scheduled_at=datetime(2030, 5, 1, 15, 0, tzinfo=UTC),
    )
    RecruitmentEvent.objects.create(
        application=application,
        event_type=RecruitmentEvent.EventType.FOLLOW_UP,
        scheduled_at=datetime(2030, 5, 1, 14, 0, tzinfo=UTC),
    )
    RecruitmentEvent.objects.create(
        application=application,
        event_type=RecruitmentEvent.EventType.ASSESSMENT,
        status=RecruitmentEvent.Status.COMPLETED,
        scheduled_at=datetime(2030, 5, 1, 13, 0, tzinfo=UTC),
    )
    RecruitmentEvent.objects.create(
        application=application,
        event_type=RecruitmentEvent.EventType.DEADLINE,
        scheduled_at=timezone.now() - timedelta(days=1),
    )
    client = Client()
    client.force_login(account)

    dashboard = client.get(reverse("dashboard"))
    content = dashboard.content

    assert dashboard.status_code == 200
    assert b"Upcoming recruitment events" in content
    assert b"Interview" in content
    assert b"Follow-up" in content
    assert b"Assessment" not in content
    assert content.index(b"Follow-up") < content.index(b"Interview")
    assert b"May 1, 2030, 10:00" in content
    assert b"May 1, 2030, 11:00" in content
    assert reverse("application_detail", args=[application.pk]).encode() in content


@pytest.mark.django_db
def test_recruitment_events_are_scoped_to_the_authenticated_candidate() -> None:
    owner = verified_candidate("event-owner@example.com")
    intruder = verified_candidate("event-intruder@example.com")
    company, _ = create_or_reuse_company("Example", "example.com")
    application = JobApplication.objects.create(
        account=owner,
        campaign=Campaign.objects.get(account=owner),
        company=company,
        role_title="Private role",
        job_description="Private job description.",
    )
    event = RecruitmentEvent.objects.create(
        application=application,
        event_type=RecruitmentEvent.EventType.CUSTOM,
        custom_title="Private event",
        scheduled_at=datetime(2030, 5, 1, 15, 0, tzinfo=UTC),
    )
    client = Client()
    client.force_login(intruder)

    dashboard = client.get(reverse("dashboard"))
    edit = client.post(
        reverse("recruitment_event_edit", args=[application.pk, event.pk]),
        {
            "event_type": RecruitmentEvent.EventType.CUSTOM,
            "custom_title": "Changed",
            "scheduled_at": "2030-05-02T15:30",
            "status": RecruitmentEvent.Status.CANCELLED,
        },
    )

    assert b"Private event" not in dashboard.content
    assert edit.status_code == 404
    event.refresh_from_db()
    assert event.custom_title == "Private event"
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


@pytest.mark.django_db
def test_candidate_can_make_flexible_stage_transitions_and_view_append_only_history() -> None:
    account = verified_candidate("stage-candidate@example.com")
    company, _ = create_or_reuse_company("Example", "example.com")
    application = JobApplication.objects.create(
        account=account,
        campaign=Campaign.objects.get(account=account),
        company=company,
        role_title="Platform engineer",
        job_description="Build dependable internal systems.",
    )
    client = Client()
    client.force_login(account)

    submitted = client.post(
        reverse("application_detail", args=[application.pk]),
        {"stage": JobApplication.Stage.SUBMITTED},
    )
    application.refresh_from_db()
    first_submission = application.first_submitted_at
    accepted = client.post(
        reverse("application_detail", args=[application.pk]),
        {"stage": JobApplication.Stage.ACCEPTED},
    )
    corrected = client.post(
        reverse("application_detail", args=[application.pk]),
        {"stage": JobApplication.Stage.INTERVIEWING},
    )
    detail = client.get(reverse("application_detail", args=[application.pk]))

    assert submitted.status_code == 302
    assert accepted.status_code == 302
    assert corrected.status_code == 302
    application.refresh_from_db()
    assert application.stage == JobApplication.Stage.INTERVIEWING
    assert application.first_submitted_at == first_submission
    assert first_submission is not None
    assert list(application.stage_transitions.values_list("from_stage", "to_stage")) == [
        (JobApplication.Stage.DRAFT, JobApplication.Stage.SUBMITTED),
        (JobApplication.Stage.SUBMITTED, JobApplication.Stage.ACCEPTED),
        (JobApplication.Stage.ACCEPTED, JobApplication.Stage.INTERVIEWING),
    ]
    assert detail.status_code == 200
    assert b"Stage history" in detail.content
    assert b"Submitted" in detail.content
    assert b"Accepted" in detail.content


@pytest.mark.django_db
def test_stage_transition_rolls_back_current_stage_and_submission_time_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = verified_candidate("atomic-stage@example.com")
    company, _ = create_or_reuse_company("Example", "example.com")
    application = JobApplication.objects.create(
        account=account,
        campaign=Campaign.objects.get(account=account),
        company=company,
        role_title="Platform engineer",
        job_description="Build dependable internal systems.",
    )

    def fail_transition_creation(*args: object, **kwargs: object) -> object:
        raise RuntimeError("Transition persistence failed")

    monkeypatch.setattr(StageTransition.objects, "create", fail_transition_creation)

    with pytest.raises(RuntimeError):
        transition_application(
            account=account,
            application_id=application.pk,
            stage=JobApplication.Stage.SUBMITTED,
        )

    application.refresh_from_db()
    assert application.stage == JobApplication.Stage.DRAFT
    assert application.first_submitted_at is None
    assert not application.stage_transitions.exists()


@pytest.mark.django_db
def test_stage_transitions_and_progress_are_scoped_to_the_authenticated_candidate() -> None:
    owner = verified_candidate("stage-owner@example.com")
    intruder = verified_candidate("stage-intruder@example.com")
    company, _ = create_or_reuse_company("Example", "example.com")
    application = JobApplication.objects.create(
        account=owner,
        campaign=Campaign.objects.get(account=owner),
        company=company,
        role_title="Private role",
        job_description="Private job description.",
    )
    client = Client()
    client.force_login(intruder)

    response = client.post(
        reverse("application_detail", args=[application.pk]),
        {"stage": JobApplication.Stage.SUBMITTED},
    )
    dashboard = client.get(reverse("dashboard"))

    assert response.status_code == 404
    assert b"Private role" not in dashboard.content
    application.refresh_from_db()
    assert application.stage == JobApplication.Stage.DRAFT


@pytest.mark.django_db
def test_confirmed_deletion_cascades_and_recalculates_progress() -> None:
    account = verified_candidate("delete-candidate@example.com")
    campaign = Campaign.objects.get(account=account)
    company, _ = create_or_reuse_company("Example", "example.com")
    application = JobApplication.objects.create(
        account=account,
        campaign=campaign,
        company=company,
        role_title="Platform engineer",
        job_description="Build dependable internal systems.",
    )
    transition_application(
        account=account,
        application_id=application.pk,
        stage=JobApplication.Stage.SUBMITTED,
    )
    RecruitmentEvent.objects.create(
        application=application,
        event_type=RecruitmentEvent.EventType.INTERVIEW,
        scheduled_at=datetime(2030, 5, 1, 15, 0, tzinfo=UTC),
    )
    client = Client()
    client.force_login(account)

    confirmation = client.get(reverse("application_delete", args=[application.pk]))
    cancelled = client.post(
        reverse("application_delete", args=[application.pk]),
        {"cancel": "1"},
    )
    deleted = client.post(
        reverse("application_delete", args=[application.pk]),
        {"confirm": "1"},
        headers={"HX-Request": "true"},
    )
    dashboard = client.get(reverse("dashboard"))

    assert confirmation.status_code == 200
    assert b"permanently delete" in confirmation.content
    assert b"cannot be undone" in confirmation.content
    assert b"Campaign Progress" in confirmation.content
    assert b"remove its first submission" in confirmation.content
    assert cancelled.status_code == 302
    assert cancelled.headers["Location"] == reverse("application_detail", args=[application.pk])
    assert deleted.status_code == 200
    assert deleted.headers["HX-Redirect"] == reverse("dashboard")
    assert not JobApplication.objects.filter(pk=application.pk).exists()
    assert not StageTransition.objects.filter(application_id=application.pk).exists()
    assert not RecruitmentEvent.objects.filter(application_id=application.pk).exists()
    assert Campaign.objects.filter(pk=campaign.pk).exists()
    assert Company.objects.filter(pk=company.pk).exists()
    assert b"0 / 5" in dashboard.content
    assert b"0 / 20" in dashboard.content


@pytest.mark.django_db
def test_deleting_a_draft_explains_that_progress_is_unchanged() -> None:
    account = verified_candidate("delete-draft@example.com")
    campaign = Campaign.objects.get(account=account)
    company, _ = create_or_reuse_company("Example", "example.com")
    application = JobApplication.objects.create(
        account=account,
        campaign=campaign,
        company=company,
        role_title="Platform engineer",
        job_description="Build dependable internal systems.",
    )
    client = Client()
    client.force_login(account)

    confirmation = client.get(reverse("application_delete", args=[application.pk]))
    deleted = client.post(
        reverse("application_delete", args=[application.pk]),
        {"confirm": "1"},
    )

    assert confirmation.status_code == 200
    assert b"does not contribute to Campaign Progress" in confirmation.content
    assert deleted.status_code == 302
    assert deleted.headers["Location"] == reverse("dashboard")
    assert not JobApplication.objects.filter(pk=application.pk).exists()


@pytest.mark.django_db
def test_application_deletion_is_scoped_to_the_authenticated_candidate() -> None:
    owner = verified_candidate("delete-owner@example.com")
    intruder = verified_candidate("delete-intruder@example.com")
    company, _ = create_or_reuse_company("Example", "example.com")
    application = JobApplication.objects.create(
        account=owner,
        campaign=Campaign.objects.get(account=owner),
        company=company,
        role_title="Private role",
        job_description="Private job description.",
    )
    client = Client()
    client.force_login(intruder)

    confirmation = client.get(reverse("application_delete", args=[application.pk]))
    attempted_delete = client.post(
        reverse("application_delete", args=[application.pk]),
        {"confirm": "1"},
    )

    assert confirmation.status_code == 404
    assert attempted_delete.status_code == 404
    assert JobApplication.objects.filter(pk=application.pk).exists()


@pytest.mark.django_db
def test_candidate_can_add_skill_requirements_with_aliases_unknown_labels_and_htmx() -> None:
    account = verified_candidate("requirement-add@example.com")
    company, _ = create_or_reuse_company("Example", "example.com")
    application = JobApplication.objects.create(
        account=account,
        campaign=Campaign.objects.get(account=account),
        company=company,
        role_title="Platform engineer",
        job_description="Build dependable systems.",
    )
    concept = SkillConcept.objects.create(canonical_name="Node.js")
    SkillAlias.objects.create(concept=concept, display_name="NodeJS")
    client = Client()
    client.force_login(account)

    canonical = client.post(
        reverse("application_skill_requirement_create", args=[application.pk]),
        {"label": "NodeJS", "classification": ApplicationSkillRequirement.Classification.REQUIRED},
    )
    unknown = client.post(
        reverse("application_skill_requirement_create", args=[application.pk]),
        {
            "label": "  Kubernetes  ",
            "classification": ApplicationSkillRequirement.Classification.PREFERRED,
        },
        headers={"HX-Request": "true"},
    )
    detail = client.get(reverse("application_detail", args=[application.pk]))

    assert canonical.status_code == 302
    assert unknown.status_code == 200
    assert unknown.headers["HX-Redirect"] == reverse("application_detail", args=[application.pk])
    assert ApplicationSkillRequirement.objects.filter(application=application).count() == 2
    node_requirement = ApplicationSkillRequirement.objects.get(concept=concept)
    assert node_requirement.label == "NodeJS"
    assert node_requirement.classification == ApplicationSkillRequirement.Classification.REQUIRED
    kubernetes_requirement = ApplicationSkillRequirement.objects.get(label="Kubernetes")
    assert (
        kubernetes_requirement.classification
        == ApplicationSkillRequirement.Classification.PREFERRED
    )
    assert b"Required skill requirements" in detail.content
    assert b"Preferred skill requirements" in detail.content
    assert b"Node.js" in detail.content
    assert b"Kubernetes" in detail.content
    removed = client.post(
        reverse(
            "application_skill_requirement_delete",
            args=[application.pk, kubernetes_requirement.pk],
        ),
        {},
        headers={"HX-Request": "true"},
    )
    assert removed.status_code == 200
    assert removed.headers["HX-Redirect"] == reverse("application_detail", args=[application.pk])
    assert not ApplicationSkillRequirement.objects.filter(pk=kubernetes_requirement.pk).exists()


@pytest.mark.django_db
def test_candidate_can_edit_requirement_wording_without_remapping_and_can_deliberately_remap() -> (
    None
):
    account = verified_candidate("requirement-edit@example.com")
    company, _ = create_or_reuse_company("Example", "example.com")
    application = JobApplication.objects.create(
        account=account,
        campaign=Campaign.objects.get(account=account),
        company=company,
        role_title="Platform engineer",
        job_description="Build dependable systems.",
    )
    node, _ = SkillConcept.objects.get_or_create(
        canonical_key="node", defaults={"canonical_name": "Node"}
    )
    django = SkillConcept.objects.create(canonical_name="Django")
    requirement = ApplicationSkillRequirement.objects.create(
        application=application,
        concept=node,
        label="Node.js",
        classification=ApplicationSkillRequirement.Classification.REQUIRED,
    )
    client = Client()
    client.force_login(account)

    edited = client.post(
        reverse("application_skill_requirement_edit", args=[application.pk, requirement.pk]),
        {
            "label": "Node.js in production",
            "classification": ApplicationSkillRequirement.Classification.PREFERRED,
        },
    )
    requirement.refresh_from_db()
    assert requirement.label == "Node.js in production"
    assert requirement.classification == ApplicationSkillRequirement.Classification.PREFERRED
    assert requirement.concept_id == node.pk
    remapped = client.post(
        reverse("application_skill_requirement_remap", args=[application.pk, requirement.pk]),
        {"label": "Django"},
        headers={"HX-Request": "true"},
    )

    assert edited.status_code == 302
    assert remapped.status_code == 200
    assert remapped.headers["HX-Redirect"] == reverse("application_detail", args=[application.pk])
    requirement.refresh_from_db()
    assert requirement.label == "Django"
    assert requirement.concept_id == django.pk


@pytest.mark.django_db
def test_requirement_duplicate_and_invalid_input_returns_focused_feedback_without_records() -> None:
    account = verified_candidate("requirement-validation@example.com")
    company, _ = create_or_reuse_company("Example", "example.com")
    application = JobApplication.objects.create(
        account=account,
        campaign=Campaign.objects.get(account=account),
        company=company,
        role_title="Platform engineer",
        job_description="Build dependable systems.",
    )
    concept, _ = SkillConcept.objects.get_or_create(
        canonical_key="python",
        defaults={"canonical_name": "Python"},
    )
    SkillAlias.objects.get_or_create(
        concept=concept, normalized_value="py", defaults={"display_name": "py"}
    )
    requirement = ApplicationSkillRequirement.objects.create(
        application=application,
        concept=concept,
        label="Python",
        classification=ApplicationSkillRequirement.Classification.REQUIRED,
    )
    client = Client()
    client.force_login(account)

    duplicate = client.post(
        reverse("application_skill_requirement_create", args=[application.pk]),
        {"label": "PY", "classification": ApplicationSkillRequirement.Classification.PREFERRED},
    )
    blank = client.post(
        reverse("application_skill_requirement_create", args=[application.pk]),
        {"label": " ", "classification": ApplicationSkillRequirement.Classification.REQUIRED},
    )
    invalid_classification = client.post(
        reverse("application_skill_requirement_create", args=[application.pk]),
        {"label": "Rust", "classification": "mandatory"},
    )

    assert duplicate.status_code == 200
    assert b"already has this skill requirement" in duplicate.content
    assert blank.status_code == 200
    assert b"Enter a hard-skill label." in blank.content
    assert invalid_classification.status_code == 200
    assert b"Select a valid choice" in invalid_classification.content
    assert ApplicationSkillRequirement.objects.count() == 1
    assert SkillConcept.objects.filter(canonical_key="rust").count() == 0
    assert requirement.concept_id == concept.pk


@pytest.mark.django_db
def test_skill_requirements_are_private_and_cascade_without_deleting_shared_catalog() -> None:
    owner = verified_candidate("requirement-owner@example.com")
    intruder = verified_candidate("requirement-intruder@example.com")
    company, _ = create_or_reuse_company("Example", "example.com")
    concept, _ = SkillConcept.objects.get_or_create(
        canonical_key="python",
        defaults={"canonical_name": "Python"},
    )
    application = JobApplication.objects.create(
        account=owner,
        campaign=Campaign.objects.get(account=owner),
        company=company,
        role_title="Private role",
        job_description="Private job description.",
    )
    requirement = ApplicationSkillRequirement.objects.create(
        application=application,
        concept=concept,
        label="Python",
        classification=ApplicationSkillRequirement.Classification.REQUIRED,
    )
    client = Client()
    client.force_login(intruder)

    detail = client.get(reverse("application_detail", args=[application.pk]))
    create = client.post(
        reverse("application_skill_requirement_create", args=[application.pk]),
        {"label": "Django", "classification": ApplicationSkillRequirement.Classification.REQUIRED},
    )
    edit = client.post(
        reverse("application_skill_requirement_edit", args=[application.pk, requirement.pk]),
        {
            "label": "Changed",
            "classification": ApplicationSkillRequirement.Classification.PREFERRED,
        },
    )
    remove = client.post(
        reverse("application_skill_requirement_delete", args=[application.pk, requirement.pk]),
        {},
    )
    remap = client.post(
        reverse("application_skill_requirement_remap", args=[application.pk, requirement.pk]),
        {"label": "Django"},
    )

    assert detail.status_code == 404
    assert create.status_code == 404
    assert edit.status_code == 404
    assert remove.status_code == 404
    assert remap.status_code == 404
    assert ApplicationSkillRequirement.objects.filter(pk=requirement.pk).exists()

    owner_client = Client()
    owner_client.force_login(owner)
    deleted = owner_client.post(
        reverse("application_delete", args=[application.pk]), {"confirm": "1"}
    )

    assert deleted.status_code == 302
    assert not ApplicationSkillRequirement.objects.filter(pk=requirement.pk).exists()
    assert SkillConcept.objects.filter(pk=concept.pk).exists()
    assert SkillAlias.objects.filter(concept=concept).exists()
