from datetime import UTC, datetime
from typing import NoReturn

import pytest
from allauth.account.models import EmailAddress
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Account
from apps.accounts.services import delete_account
from apps.applications.models import (
    Company,
    CompanyDomainAlias,
    JobApplication,
    RecruitmentEvent,
    StageTransition,
)
from apps.applications.services import transition_application
from apps.campaigns.models import Campaign
from apps.profiles.models import (
    CandidateProfile,
    Education,
    Experience,
    Highlight,
    Language,
    ProfileSkill,
    Project,
    ProjectSkill,
)
from apps.skills.models import SkillConcept
from apps.skills.services import resolve_skill_label

pytestmark = pytest.mark.integration


def verified_candidate(email: str, full_name: str = "Ada Lovelace") -> Account:
    account = Account.objects.create_user(email, "a-secure-password")
    EmailAddress.objects.create(user=account, email=account.email, primary=True, verified=True)
    CandidateProfile.objects.create(
        account=account,
        full_name=full_name,
        timezone="Europe/London",
    )
    Campaign.objects.create(
        account=account,
        weekly_target=5,
        monthly_target=20,
        timezone="Europe/London",
    )
    return account


def create_private_data(account: Account, company: Company) -> JobApplication:
    profile = CandidateProfile.objects.get(account=account)
    experience = Experience.objects.create(
        profile=profile,
        role="Senior engineer",
        organization="Analytical Engines Ltd",
        location="London",
        start_date="2020-01-01",
    )
    Highlight.objects.create(experience=experience, text="Built reliable systems.")
    Education.objects.create(
        profile=profile,
        institution="University of London",
        degree="Mathematics",
        start_date="2015-01-01",
    )
    Project.objects.create(profile=profile, name="Analytical engine simulator")
    concept, _ = resolve_skill_label("Python")
    ProfileSkill.objects.create(profile=profile, concept=concept, label="Python")
    Language.objects.create(
        profile=profile,
        name="English",
        proficiency=Language.Proficiency.NATIVE,
    )
    application = JobApplication.objects.create(
        account=account,
        campaign=Campaign.objects.get(account=account),
        company=company,
        role_title="Platform engineer",
        job_description="Build dependable internal systems.",
        private_notes="Private interview preparation.",
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
    return application


@pytest.mark.django_db
def test_candidate_can_cancel_account_deletion_without_changing_private_or_shared_data() -> None:
    account = verified_candidate("cancel-delete@example.com")
    company = Company.objects.create(name="Example Careers", canonical_domain="example.com")
    alias = CompanyDomainAlias.objects.create(company=company, domain="example.org")
    application = create_private_data(account, company)
    client = Client()
    client.force_login(account)

    confirmation = client.get(reverse("account_delete"))
    cancelled = client.post(reverse("account_delete"), {"cancel": "1"})

    assert confirmation.status_code == 200
    assert b"permanently deletes your account" in confirmation.content
    assert b"Shared public Companies and their aliases" in confirmation.content
    assert cancelled.status_code == 302
    assert cancelled.headers["Location"] == reverse("profile")
    assert Account.objects.filter(pk=account.pk).exists()
    assert CandidateProfile.objects.filter(account=account).exists()
    assert JobApplication.objects.filter(pk=application.pk).exists()
    assert Company.objects.filter(pk=company.pk).exists()
    assert CompanyDomainAlias.objects.filter(pk=alias.pk, company=company).exists()


@pytest.mark.django_db
def test_confirmed_account_deletion_removes_private_data_and_preserves_shared_data() -> None:
    account = verified_candidate("delete-candidate@example.com")
    other_account = verified_candidate("other-candidate@example.com", "Grace Hopper")
    company = Company.objects.create(name="Example Careers", canonical_domain="example.com")
    alias = CompanyDomainAlias.objects.create(company=company, domain="example.org")
    deleted_application = create_private_data(account, company)
    profile_id = CandidateProfile.objects.get(account=account).pk
    project = Project.objects.get(profile_id=profile_id)
    concept = SkillConcept.objects.create(canonical_name="Django")
    ProjectSkill.objects.create(project=project, concept=concept, label="Django")
    retained_application = JobApplication.objects.create(
        account=other_account,
        campaign=Campaign.objects.get(account=other_account),
        company=company,
        role_title="Other candidate role",
        job_description="Other candidate description.",
        private_notes="Other candidate private note.",
    )
    client = Client()
    client.force_login(account)

    response = client.post(reverse("account_delete"), {"confirm": "1"})

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("home")
    assert not Account.objects.filter(pk=account.pk).exists()
    assert not EmailAddress.objects.filter(user_id=account.pk).exists()
    assert not CandidateProfile.objects.filter(account_id=account.pk).exists()
    assert not Experience.objects.filter(profile_id=profile_id).exists()
    assert not Highlight.objects.filter(experience__profile_id=profile_id).exists()
    assert not Education.objects.filter(profile_id=profile_id).exists()
    assert not Project.objects.filter(profile_id=profile_id).exists()
    assert not ProjectSkill.objects.filter(project_id=project.pk).exists()
    assert not ProfileSkill.objects.filter(profile_id=profile_id).exists()
    assert not Language.objects.filter(profile_id=profile_id).exists()
    assert not Campaign.objects.filter(account_id=account.pk).exists()
    assert not JobApplication.objects.filter(account_id=account.pk).exists()
    assert not StageTransition.objects.filter(application_id=deleted_application.pk).exists()
    assert not RecruitmentEvent.objects.filter(application_id=deleted_application.pk).exists()
    assert Company.objects.filter(pk=company.pk, name="Example Careers").exists()
    assert CompanyDomainAlias.objects.filter(pk=alias.pk, company=company).exists()
    assert SkillConcept.objects.filter(pk=concept.pk).exists()
    assert SkillConcept.objects.filter(canonical_key="python").exists()
    retained_application.refresh_from_db()
    assert retained_application.company_id == company.pk
    assert retained_application.private_notes == "Other candidate private note."
    assert client.login(username=account.email, password="a-secure-password") is False
    assert client.get(reverse("dashboard")).status_code == 302


@pytest.mark.django_db
def test_account_deletion_rolls_back_when_account_removal_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = verified_candidate("failed-delete@example.com")
    company = Company.objects.create(name="Example Careers")
    application = create_private_data(account, company)
    profile_id = CandidateProfile.objects.get(account=account).pk
    original_delete = Account.delete

    def fail_after_delete(self: Account, *args: object, **kwargs: object) -> NoReturn:
        original_delete(self, *args, **kwargs)
        raise RuntimeError("account removal failed")

    monkeypatch.setattr(Account, "delete", fail_after_delete)

    with pytest.raises(RuntimeError, match="account removal failed"):
        delete_account(account=account)

    assert Account.objects.filter(pk=account.pk).exists()
    assert CandidateProfile.objects.filter(account=account).exists()
    assert Experience.objects.filter(profile_id=profile_id).exists()
    assert Highlight.objects.filter(experience__profile_id=profile_id).exists()
    assert Education.objects.filter(profile_id=profile_id).exists()
    assert Project.objects.filter(profile_id=profile_id).exists()
    assert ProfileSkill.objects.filter(profile_id=profile_id).exists()
    assert Language.objects.filter(profile_id=profile_id).exists()
    assert Campaign.objects.filter(account=account).exists()
    assert JobApplication.objects.filter(pk=application.pk).exists()
    assert StageTransition.objects.filter(application_id=application.pk).exists()
    assert RecruitmentEvent.objects.filter(application_id=application.pk).exists()
    assert Company.objects.filter(pk=company.pk).exists()
