from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from allauth.account.models import EmailAddress
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Account
from apps.applications.models import (
    Company,
    CompanyDomainAlias,
    JobApplication,
    RecruitmentEvent,
    StageTransition,
)
from apps.applications.services import create_or_reuse_company, transition_application
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
    assert b"Send portfolio follow-up" in client.get(
        reverse("application_detail", args=[application.pk])
    ).content


@pytest.mark.django_db
def test_dashboard_shows_only_upcoming_scheduled_events_in_candidate_time_and_application_order(
) -> None:
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
