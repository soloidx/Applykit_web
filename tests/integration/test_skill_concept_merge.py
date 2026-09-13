from datetime import date

import pytest
from allauth.account.models import EmailAddress
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Account
from apps.applications.models import ApplicationSkillRequirement, JobApplication
from apps.applications.services import create_or_reuse_company
from apps.campaigns.models import Campaign
from apps.profiles.models import (
    CandidateProfile,
    Experience,
    ExperienceSkill,
    ProfileSkill,
    Project,
    ProjectSkill,
)
from apps.resumes.models import Resume, ResumeSkill
from apps.skills.models import SkillAlias, SkillConcept, SkillConceptMergeAudit
from apps.skills.services import (
    StaleCatalogPreviewError,
    create_skill_alias,
    merge_skill_concept,
    preview_skill_concept_merge,
    resolve_skill_label,
    skill_catalog_issues,
)

pytestmark = pytest.mark.integration

REQUIRED = ApplicationSkillRequirement.Classification.REQUIRED
PREFERRED = ApplicationSkillRequirement.Classification.PREFERRED


def administrator() -> Account:
    return Account.objects.create_superuser("admin@example.com", "a-secure-password")


def candidate(email: str = "candidate@example.com") -> Account:
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


def profile_of(account: Account) -> CandidateProfile:
    return CandidateProfile.objects.get(account=account)


def application(account: Account, company_name: str = "Example Careers") -> JobApplication:
    company, _ = create_or_reuse_company(company_name)
    return JobApplication.objects.create(
        account=account,
        campaign=Campaign.objects.get(account=account),
        company=company,
        role_title="Platform engineer",
        job_description="Build dependable internal systems.",
    )


def canonical_alias(concept: SkillConcept) -> SkillAlias:
    return SkillAlias.objects.get(concept=concept, is_canonical=True)


def requirement(
    application_: JobApplication,
    alias: SkillAlias,
    classification: str,
) -> ApplicationSkillRequirement:
    return ApplicationSkillRequirement.objects.create(
        application=application_,
        alias=alias,
        classification=classification,
    )


def merge_preview(
    administrator_: Account,
    loser: SkillConcept,
    survivor: SkillConcept,
):
    return preview_skill_concept_merge(
        loser=loser,
        survivor=survivor,
        actor=administrator_,
    )


def confirm_merge(
    administrator_: Account,
    loser: SkillConcept,
    survivor: SkillConcept,
    preview,
):
    return merge_skill_concept(
        loser=loser,
        survivor=survivor,
        actor=administrator_,
        reason="Merge duplicate concepts.",
        preview_token=preview.token,
    )


@pytest.mark.django_db
def test_merge_preview_requires_an_administrator() -> None:
    account = candidate()
    loser, _ = resolve_skill_label("Python")
    survivor, _ = resolve_skill_label("Django")

    with pytest.raises(ValidationError):
        preview_skill_concept_merge(loser=loser, survivor=survivor, actor=account)


@pytest.mark.django_db
def test_merge_confirmation_requires_an_administrator() -> None:
    administrator_ = administrator()
    account = candidate()
    loser, _ = resolve_skill_label("Python")
    survivor, _ = resolve_skill_label("Django")
    preview = merge_preview(administrator_, loser, survivor)

    with pytest.raises(ValidationError):
        merge_skill_concept(
            loser=loser,
            survivor=survivor,
            actor=account,
            reason="Repair",
            preview_token=preview.token,
        )

    assert SkillConcept.objects.filter(pk=loser.pk).exists()


@pytest.mark.django_db
def test_merge_requires_a_reason() -> None:
    administrator_ = administrator()
    loser, _ = resolve_skill_label("Python")
    survivor, _ = resolve_skill_label("Django")
    preview = merge_preview(administrator_, loser, survivor)

    with pytest.raises(ValidationError):
        merge_skill_concept(
            loser=loser,
            survivor=survivor,
            actor=administrator_,
            reason="   ",
            preview_token=preview.token,
        )

    assert SkillConcept.objects.filter(pk=loser.pk).exists()


@pytest.mark.django_db
def test_merge_rejects_the_same_concept() -> None:
    administrator_ = administrator()
    loser, _ = resolve_skill_label("Python")

    with pytest.raises(ValidationError):
        merge_preview(administrator_, loser, loser)


@pytest.mark.django_db
def test_merge_requires_a_preview_token() -> None:
    administrator_ = administrator()
    loser, _ = resolve_skill_label("Python")
    survivor, _ = resolve_skill_label("Django")

    with pytest.raises(ValidationError):
        merge_skill_concept(
            loser=loser,
            survivor=survivor,
            actor=administrator_,
            reason="Repair",
            preview_token="",
        )


@pytest.mark.django_db
def test_preview_lists_aliases_and_private_references_without_mutating() -> None:
    administrator_ = administrator()
    account = candidate()
    loser, _ = resolve_skill_label("Python")
    create_skill_alias(concept=loser, display_name="py")
    survivor, _ = resolve_skill_label("Django")
    application_ = application(account)
    requirement(application_, canonical_alias(loser), REQUIRED)
    ProfileSkill.objects.create(profile=profile_of(account), concept=loser)

    preview = merge_preview(administrator_, loser, survivor)

    assert preview.loser_concept_id == loser.pk
    assert preview.survivor_concept_id == survivor.pk
    assert preview.token
    assert {alias.display_name for alias in preview.aliases} == {"Python", "py"}
    assert preview.requirement_ids == tuple(
        ApplicationSkillRequirement.objects.filter(application=application_).values_list(
            "pk", flat=True
        )
    )
    assert len(preview.profile_skill_ids) == 1
    assert preview.skill_collisions == ()
    assert preview.requirement_collisions == ()
    assert preview.resume_collisions == ()

    loser.refresh_from_db()
    assert loser.canonical_name == "Python"
    assert SkillConcept.objects.filter(pk=survivor.pk).exists()
    assert SkillAlias.objects.filter(concept=loser).count() == 2


@pytest.mark.django_db
def test_preview_reports_discarded_rows_classification_changes_and_resume_outcomes() -> None:
    administrator_ = administrator()
    account = candidate()
    loser, _ = resolve_skill_label("Python")
    survivor, _ = resolve_skill_label("Django")
    application_ = application(account)
    source_requirement = requirement(application_, canonical_alias(loser), REQUIRED)
    kept_requirement = requirement(application_, canonical_alias(survivor), PREFERRED)
    profile = profile_of(account)
    loser_skill = ProfileSkill.objects.create(profile=profile, concept=loser)
    survivor_skill = ProfileSkill.objects.create(profile=profile, concept=survivor)
    ProfileSkill.objects.filter(pk=survivor_skill.pk).update(position=3)
    ProfileSkill.objects.filter(pk=loser_skill.pk).update(position=1)
    resume = Resume.objects.create(application=application_)
    loser_resume_skill = ResumeSkill.objects.create(
        resume=resume, concept=loser, included=True, position=0
    )
    survivor_resume_skill = ResumeSkill.objects.create(
        resume=resume, concept=survivor, included=False, position=4, label_override="Custom"
    )

    preview = merge_preview(administrator_, loser, survivor)

    assert len(preview.skill_collisions) == 1
    skill_collision = preview.skill_collisions[0]
    assert skill_collision.kind == "profile"
    assert skill_collision.kept_id == survivor_skill.pk
    assert skill_collision.discarded_id == loser_skill.pk
    assert skill_collision.kept_position == 1
    assert len(preview.requirement_collisions) == 1
    requirement_collision = preview.requirement_collisions[0]
    assert requirement_collision.application_id == application_.pk
    assert requirement_collision.kept_requirement_id == kept_requirement.pk
    assert requirement_collision.discarded_requirement_ids == (source_requirement.pk,)
    assert requirement_collision.kept_classification == REQUIRED
    assert requirement_collision.promoted is True
    assert len(preview.resume_collisions) == 1
    resume_collision = preview.resume_collisions[0]
    assert resume_collision.resume_id == resume.pk
    assert resume_collision.kept_resume_skill_id == survivor_resume_skill.pk
    assert resume_collision.discarded_resume_skill_id == loser_resume_skill.pk
    assert resume_collision.included is False
    assert resume_collision.position == 4
    assert resume_collision.has_label_override is True
    assert preview.promoted_requirement_ids == (kept_requirement.pk,)
    assert preview.discarded_requirement_ids == (source_requirement.pk,)
    assert preview.discarded_profile_skill_ids == (loser_skill.pk,)
    assert preview.discarded_resume_skill_ids == (loser_resume_skill.pk,)


@pytest.mark.django_db
def test_no_collision_merge_moves_aliases_references_and_deletes_the_loser() -> None:
    administrator_ = administrator()
    account = candidate()
    loser, _ = resolve_skill_label("Python")
    variant = create_skill_alias(concept=loser, display_name="py")[0]
    survivor, _ = resolve_skill_label("Django")
    application_ = application(account)
    moved_requirement = requirement(application_, variant, PREFERRED)
    profile = profile_of(account)
    moved_profile_skill = ProfileSkill.objects.create(profile=profile, concept=loser)
    experience = Experience.objects.create(
        profile=profile,
        role="Engineer",
        organization="Acme",
        location="Remote",
        start_date=date(2020, 1, 1),
    )
    moved_experience_skill = ExperienceSkill.objects.create(experience=experience, concept=loser)
    project = Project.objects.create(profile=profile, name="Side project")
    moved_project_skill = ProjectSkill.objects.create(project=project, concept=loser)
    resume = Resume.objects.create(application=application_)
    moved_resume_skill = ResumeSkill.objects.create(
        resume=resume, concept=loser, included=True, position=2, label_override="Py"
    )
    preview = merge_preview(administrator_, loser, survivor)

    result = confirm_merge(administrator_, loser, survivor, preview)

    assert not SkillConcept.objects.filter(pk=loser.pk).exists()
    moved_requirement.refresh_from_db()
    assert moved_requirement.alias_id == variant.pk
    assert moved_requirement.alias.concept_id == survivor.pk
    assert moved_requirement.classification == PREFERRED
    moved_profile_skill.refresh_from_db()
    assert moved_profile_skill.concept_id == survivor.pk
    moved_experience_skill.refresh_from_db()
    assert moved_experience_skill.concept_id == survivor.pk
    moved_project_skill.refresh_from_db()
    assert moved_project_skill.concept_id == survivor.pk
    moved_resume_skill.refresh_from_db()
    assert moved_resume_skill.concept_id == survivor.pk
    assert moved_resume_skill.included is True
    assert moved_resume_skill.position == 2
    assert moved_resume_skill.label_override == "Py"
    assert SkillAlias.objects.get(pk=variant.pk).concept_id == survivor.pk
    assert SkillAlias.objects.filter(concept=survivor, is_canonical=True).count() == 1
    assert result.audit.loser_concept_id == loser.pk
    assert skill_catalog_issues() == ()


@pytest.mark.django_db
def test_merge_moves_the_loser_canonical_alias_as_a_noncanonical_alias() -> None:
    administrator_ = administrator()
    loser, _ = resolve_skill_label("Python")
    survivor, _ = resolve_skill_label("Django")
    loser_canonical = canonical_alias(loser)
    preview = merge_preview(administrator_, loser, survivor)

    confirm_merge(administrator_, loser, survivor, preview)

    loser_canonical.refresh_from_db()
    assert loser_canonical.concept_id == survivor.pk
    assert loser_canonical.is_canonical is False
    assert SkillAlias.objects.filter(concept=survivor, is_canonical=True).count() == 1
    assert skill_catalog_issues() == ()


@pytest.mark.django_db
def test_profile_skill_collision_keeps_the_earliest_position_and_normalizes_order() -> None:
    administrator_ = administrator()
    account = candidate()
    loser, _ = resolve_skill_label("Python")
    survivor, _ = resolve_skill_label("Django")
    other, _ = resolve_skill_label("Rust")
    profile = profile_of(account)
    other_skill = ProfileSkill.objects.create(profile=profile, concept=other)
    survivor_skill = ProfileSkill.objects.create(profile=profile, concept=survivor)
    loser_skill = ProfileSkill.objects.create(profile=profile, concept=loser)
    ProfileSkill.objects.filter(pk=other_skill.pk).update(position=1)
    ProfileSkill.objects.filter(pk=survivor_skill.pk).update(position=2)
    ProfileSkill.objects.filter(pk=loser_skill.pk).update(position=0)
    preview = merge_preview(administrator_, loser, survivor)

    confirm_merge(administrator_, loser, survivor, preview)

    assert not ProfileSkill.objects.filter(pk=loser_skill.pk).exists()
    survivor_skill.refresh_from_db()
    assert survivor_skill.concept_id == survivor.pk
    assert survivor_skill.position == 0
    remaining = list(ProfileSkill.objects.filter(profile=profile).order_by("position", "id"))
    assert [skill.position for skill in remaining] == [0, 1]
    assert remaining[0].pk == survivor_skill.pk
    assert skill_catalog_issues() == ()


@pytest.mark.django_db
def test_experience_skill_collision_keeps_the_earliest_position_and_normalizes_order() -> None:
    administrator_ = administrator()
    account = candidate()
    loser, _ = resolve_skill_label("Python")
    survivor, _ = resolve_skill_label("Django")
    other, _ = resolve_skill_label("Rust")
    profile = profile_of(account)
    experience = Experience.objects.create(
        profile=profile,
        role="Engineer",
        organization="Acme",
        location="Remote",
        start_date=date(2020, 1, 1),
    )
    other_skill = ExperienceSkill.objects.create(experience=experience, concept=other)
    survivor_skill = ExperienceSkill.objects.create(experience=experience, concept=survivor)
    loser_skill = ExperienceSkill.objects.create(experience=experience, concept=loser)
    ExperienceSkill.objects.filter(pk=other_skill.pk).update(position=1)
    ExperienceSkill.objects.filter(pk=survivor_skill.pk).update(position=2)
    ExperienceSkill.objects.filter(pk=loser_skill.pk).update(position=0)
    preview = merge_preview(administrator_, loser, survivor)

    confirm_merge(administrator_, loser, survivor, preview)

    assert not ExperienceSkill.objects.filter(pk=loser_skill.pk).exists()
    survivor_skill.refresh_from_db()
    assert survivor_skill.concept_id == survivor.pk
    assert survivor_skill.position == 0
    remaining = list(
        ExperienceSkill.objects.filter(experience=experience).order_by("position", "id")
    )
    assert [skill.position for skill in remaining] == [0, 1]
    assert remaining[0].pk == survivor_skill.pk
    assert skill_catalog_issues() == ()


@pytest.mark.django_db
def test_project_skill_collision_keeps_the_earliest_position_and_normalizes_order() -> None:
    administrator_ = administrator()
    account = candidate()
    loser, _ = resolve_skill_label("Python")
    survivor, _ = resolve_skill_label("Django")
    other, _ = resolve_skill_label("Rust")
    profile = profile_of(account)
    project = Project.objects.create(profile=profile, name="Side project")
    other_skill = ProjectSkill.objects.create(project=project, concept=other)
    survivor_skill = ProjectSkill.objects.create(project=project, concept=survivor)
    loser_skill = ProjectSkill.objects.create(project=project, concept=loser)
    ProjectSkill.objects.filter(pk=other_skill.pk).update(position=1)
    ProjectSkill.objects.filter(pk=survivor_skill.pk).update(position=2)
    ProjectSkill.objects.filter(pk=loser_skill.pk).update(position=0)
    preview = merge_preview(administrator_, loser, survivor)

    confirm_merge(administrator_, loser, survivor, preview)

    assert not ProjectSkill.objects.filter(pk=loser_skill.pk).exists()
    survivor_skill.refresh_from_db()
    assert survivor_skill.concept_id == survivor.pk
    assert survivor_skill.position == 0
    remaining = list(ProjectSkill.objects.filter(project=project).order_by("position", "id"))
    assert [skill.position for skill in remaining] == [0, 1]
    assert remaining[0].pk == survivor_skill.pk
    assert skill_catalog_issues() == ()


@pytest.mark.django_db
def test_a_moved_association_without_a_collision_keeps_its_position() -> None:
    administrator_ = administrator()
    account = candidate()
    loser, _ = resolve_skill_label("Python")
    survivor, _ = resolve_skill_label("Django")
    other, _ = resolve_skill_label("Rust")
    profile = profile_of(account)
    existing = ProfileSkill.objects.create(profile=profile, concept=other)
    moved = ProfileSkill.objects.create(profile=profile, concept=loser)
    ProfileSkill.objects.filter(pk=existing.pk).update(position=5)
    ProfileSkill.objects.filter(pk=moved.pk).update(position=9)
    preview = merge_preview(administrator_, loser, survivor)

    assert preview.skill_collisions == ()

    confirm_merge(administrator_, loser, survivor, preview)

    moved.refresh_from_db()
    existing.refresh_from_db()
    assert moved.concept_id == survivor.pk
    assert moved.position == 9
    assert existing.position == 5
    assert skill_catalog_issues() == ()


@pytest.mark.django_db
def test_requirement_collision_promotes_the_survivor_row_when_either_was_required() -> None:
    administrator_ = administrator()
    account = candidate()
    loser, _ = resolve_skill_label("Python")
    survivor, _ = resolve_skill_label("Django")
    application_ = application(account)
    source_requirement = requirement(application_, canonical_alias(loser), REQUIRED)
    kept_requirement = requirement(application_, canonical_alias(survivor), PREFERRED)
    preview = merge_preview(administrator_, loser, survivor)

    result = confirm_merge(administrator_, loser, survivor, preview)

    assert not ApplicationSkillRequirement.objects.filter(pk=source_requirement.pk).exists()
    kept_requirement.refresh_from_db()
    assert kept_requirement.classification == REQUIRED
    assert kept_requirement.alias.concept_id == survivor.pk
    assert result.audit.promoted_requirement_ids == [kept_requirement.pk]
    assert result.audit.discarded_requirement_ids == [source_requirement.pk]
    assert skill_catalog_issues() == ()


@pytest.mark.django_db
def test_requirement_collision_without_a_required_row_keeps_preferred() -> None:
    administrator_ = administrator()
    account = candidate()
    loser, _ = resolve_skill_label("Python")
    survivor, _ = resolve_skill_label("Django")
    application_ = application(account)
    source_requirement = requirement(application_, canonical_alias(loser), PREFERRED)
    kept_requirement = requirement(application_, canonical_alias(survivor), PREFERRED)
    preview = merge_preview(administrator_, loser, survivor)

    result = confirm_merge(administrator_, loser, survivor, preview)

    assert not ApplicationSkillRequirement.objects.filter(pk=source_requirement.pk).exists()
    kept_requirement.refresh_from_db()
    assert kept_requirement.classification == PREFERRED
    assert result.audit.promoted_requirement_ids == []
    assert skill_catalog_issues() == ()


@pytest.mark.django_db
def test_requirement_only_on_the_loser_moves_to_the_survivor_concept() -> None:
    administrator_ = administrator()
    account = candidate()
    loser, _ = resolve_skill_label("Python")
    variant = create_skill_alias(concept=loser, display_name="py")[0]
    survivor, _ = resolve_skill_label("Django")
    application_ = application(account)
    moved = requirement(application_, variant, REQUIRED)
    preview = merge_preview(administrator_, loser, survivor)

    result = confirm_merge(administrator_, loser, survivor, preview)

    moved.refresh_from_db()
    assert moved.alias_id == variant.pk
    assert moved.alias.concept_id == survivor.pk
    assert moved.classification == REQUIRED
    assert result.audit.collision_outcomes == []
    assert skill_catalog_issues() == ()


@pytest.mark.django_db
def test_resume_skill_collision_keeps_the_survivor_row_inclusion_position_and_label() -> None:
    administrator_ = administrator()
    account = candidate()
    loser, _ = resolve_skill_label("Python")
    survivor, _ = resolve_skill_label("Django")
    application_ = application(account)
    resume = Resume.objects.create(application=application_)
    loser_resume_skill = ResumeSkill.objects.create(
        resume=resume, concept=loser, included=True, position=0, label_override="Losing"
    )
    survivor_resume_skill = ResumeSkill.objects.create(
        resume=resume, concept=survivor, included=False, position=5, label_override="Winning"
    )
    preview = merge_preview(administrator_, loser, survivor)

    confirm_merge(administrator_, loser, survivor, preview)

    assert not ResumeSkill.objects.filter(pk=loser_resume_skill.pk).exists()
    survivor_resume_skill.refresh_from_db()
    assert survivor_resume_skill.concept_id == survivor.pk
    assert survivor_resume_skill.included is False
    assert survivor_resume_skill.position == 5
    assert survivor_resume_skill.label_override == "Winning"
    assert ResumeSkill.objects.filter(resume=resume).count() == 1
    assert skill_catalog_issues() == ()


@pytest.mark.django_db
def test_merging_keeps_survivor_resume_inclusion_when_the_loser_was_included() -> None:
    administrator_ = administrator()
    account = candidate()
    loser, _ = resolve_skill_label("Python")
    survivor, _ = resolve_skill_label("Django")
    application_ = application(account)
    resume = Resume.objects.create(application=application_)
    loser_resume_skill = ResumeSkill.objects.create(
        resume=resume, concept=loser, included=True, position=2
    )
    survivor_resume_skill = ResumeSkill.objects.create(
        resume=resume, concept=survivor, included=False, position=1
    )
    preview = merge_preview(administrator_, loser, survivor)

    confirm_merge(administrator_, loser, survivor, preview)

    assert not ResumeSkill.objects.filter(pk=loser_resume_skill.pk).exists()
    survivor_resume_skill.refresh_from_db()
    assert survivor_resume_skill.included is False
    assert survivor_resume_skill.position == 1


@pytest.mark.django_db
def test_a_stale_preview_cannot_be_confirmed_and_changes_nothing() -> None:
    administrator_ = administrator()
    account = candidate()
    loser, _ = resolve_skill_label("Python")
    survivor, _ = resolve_skill_label("Django")
    application_ = application(account)
    moved = requirement(application_, canonical_alias(loser), PREFERRED)
    preview = merge_preview(administrator_, loser, survivor)
    # A concurrent edit changes a requirement the previewed plan depends on.
    ApplicationSkillRequirement.objects.filter(pk=moved.pk).update(classification=REQUIRED)

    with pytest.raises(StaleCatalogPreviewError):
        confirm_merge(administrator_, loser, survivor, preview)

    moved.refresh_from_db()
    assert moved.classification == REQUIRED
    assert SkillConcept.objects.filter(pk=loser.pk).exists()
    assert SkillAlias.objects.filter(concept=loser).exists()


@pytest.mark.django_db
def test_an_unrelated_requirement_change_does_not_stale_the_preview() -> None:
    administrator_ = administrator()
    account = candidate()
    loser, _ = resolve_skill_label("Python")
    survivor, _ = resolve_skill_label("Django")
    unrelated, _ = resolve_skill_label("Rust")
    application_ = application(account)
    moved = requirement(application_, canonical_alias(loser), PREFERRED)
    preview = merge_preview(administrator_, loser, survivor)
    unrelated_requirement = requirement(application_, canonical_alias(unrelated), REQUIRED)

    result = confirm_merge(administrator_, loser, survivor, preview)

    moved.refresh_from_db()
    assert moved.alias.concept_id == survivor.pk
    assert unrelated_requirement.pk not in result.audit.affected_requirement_ids
    assert skill_catalog_issues() == ()


@pytest.mark.django_db
def test_failed_merge_persistence_rolls_back_everything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    administrator_ = administrator()
    account = candidate()
    loser, _ = resolve_skill_label("Python")
    variant = create_skill_alias(concept=loser, display_name="py")[0]
    survivor, _ = resolve_skill_label("Django")
    application_ = application(account)
    source_requirement = requirement(application_, variant, REQUIRED)
    survivor_requirement = requirement(application_, canonical_alias(survivor), PREFERRED)
    profile = profile_of(account)
    loser_profile_skill = ProfileSkill.objects.create(profile=profile, concept=loser)
    resume = Resume.objects.create(application=application_)
    loser_resume_skill = ResumeSkill.objects.create(resume=resume, concept=loser, position=0)
    preview = merge_preview(administrator_, loser, survivor)

    def fail_audit(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Audit persistence failed")

    monkeypatch.setattr(SkillConceptMergeAudit.objects, "create", fail_audit)

    with pytest.raises(RuntimeError):
        confirm_merge(administrator_, loser, survivor, preview)

    assert SkillConcept.objects.filter(pk=loser.pk).exists()
    assert SkillAlias.objects.filter(pk=variant.pk, concept=loser).exists()
    assert ApplicationSkillRequirement.objects.filter(pk=source_requirement.pk).exists()
    assert ApplicationSkillRequirement.objects.filter(pk=survivor_requirement.pk).exists()
    survivor_requirement.refresh_from_db()
    assert survivor_requirement.classification == PREFERRED
    assert ProfileSkill.objects.filter(pk=loser_profile_skill.pk, concept=loser).exists()
    assert ResumeSkill.objects.filter(pk=loser_resume_skill.pk, concept=loser).exists()
    assert SkillConceptMergeAudit.objects.count() == 0
    assert skill_catalog_issues() == ()


@pytest.mark.django_db
def test_merge_audit_records_actor_reason_public_snapshots_and_relationships() -> None:
    administrator_ = administrator()
    account = candidate()
    loser, _ = resolve_skill_label("Python")
    variant = create_skill_alias(concept=loser, display_name="py")[0]
    loser_canonical = canonical_alias(loser)
    survivor, _ = resolve_skill_label("Django")
    application_ = application(account)
    source_requirement = requirement(application_, variant, REQUIRED)
    kept_requirement = requirement(application_, canonical_alias(survivor), PREFERRED)
    profile = profile_of(account)
    loser_profile_skill = ProfileSkill.objects.create(profile=profile, concept=loser)
    resume = Resume.objects.create(application=application_)
    loser_resume_skill = ResumeSkill.objects.create(resume=resume, concept=loser, position=0)
    preview = merge_preview(administrator_, loser, survivor)

    result = confirm_merge(administrator_, loser, survivor, preview)

    audit = result.audit
    assert audit.actor_id == administrator_.pk
    assert audit.actor_email == administrator_.email
    assert audit.reason == "Merge duplicate concepts."
    assert audit.loser_concept_id == loser.pk
    assert audit.loser_concept_name == "Python"
    assert audit.loser_concept_key == "python"
    assert audit.survivor_concept_id == survivor.pk
    assert audit.survivor_concept_name == "Django"
    assert audit.survivor_concept_key == "django"
    assert set(audit.moved_alias_ids) == {variant.pk, loser_canonical.pk}
    assert audit.affected_application_ids == [application_.pk]
    assert audit.affected_profile_skill_ids == [loser_profile_skill.pk]
    assert audit.affected_resume_skill_ids == [loser_resume_skill.pk]
    assert set(audit.affected_requirement_ids) == {
        source_requirement.pk,
        kept_requirement.pk,
    }
    assert audit.discarded_requirement_ids == [source_requirement.pk]
    assert audit.promoted_requirement_ids == [kept_requirement.pk]
    assert audit.discarded_profile_skill_ids == []
    assert audit.discarded_resume_skill_ids == []
    assert audit.affected_relationship_count == 4
    serialized = (
        repr(audit) + " " + str(audit.moved_alias_snapshots) + " " + str(audit.collision_outcomes)
    )
    assert "Platform engineer" not in serialized
    assert "Build dependable internal systems." not in serialized
    assert "Example Careers" not in serialized
    assert "Ada Lovelace" not in serialized


@pytest.mark.django_db
def test_merge_audits_are_immutable() -> None:
    administrator_ = administrator()
    loser, _ = resolve_skill_label("Python")
    survivor, _ = resolve_skill_label("Django")
    preview = merge_preview(administrator_, loser, survivor)
    audit = confirm_merge(administrator_, loser, survivor, preview).audit

    audit.reason = "Changed"
    with pytest.raises(ValidationError):
        audit.save()
    with pytest.raises(ValidationError):
        audit.delete()

    audit.refresh_from_db()
    assert audit.reason == "Merge duplicate concepts."


@pytest.mark.django_db
def test_catalog_stays_healthy_after_a_full_merge() -> None:
    administrator_ = administrator()
    account = candidate()
    loser, _ = resolve_skill_label("Python")
    create_skill_alias(concept=loser, display_name="py")
    survivor, _ = resolve_skill_label("Django")
    application_ = application(account)
    requirement(application_, canonical_alias(loser), REQUIRED)
    requirement(application_, canonical_alias(survivor), PREFERRED)
    profile = profile_of(account)
    ProfileSkill.objects.create(profile=profile, concept=loser)
    ProfileSkill.objects.create(profile=profile, concept=survivor)
    resume = Resume.objects.create(application=application_)
    ResumeSkill.objects.create(resume=resume, concept=loser, position=0)
    ResumeSkill.objects.create(resume=resume, concept=survivor, position=1)
    preview = merge_preview(administrator_, loser, survivor)

    confirm_merge(administrator_, loser, survivor, preview)

    assert skill_catalog_issues() == ()


@pytest.mark.django_db
def test_administrator_previews_and_confirms_a_merge_through_the_admin() -> None:
    administrator_ = administrator()
    account = candidate()
    loser, _ = resolve_skill_label("Python")
    survivor, _ = resolve_skill_label("Django")
    application_ = application(account)
    requirement(application_, canonical_alias(loser), REQUIRED)
    client = Client()
    client.force_login(administrator_)
    url = reverse("admin:skills_skillconcept_merge", args=[loser.pk])

    preview_response = client.post(
        url,
        {"survivor": str(survivor.pk), "reason": "Merge", "action": "preview"},
    )

    assert preview_response.status_code == 200
    preview = preview_response.context["preview"]
    assert preview.loser_concept_id == loser.pk
    assert application_.pk == ApplicationSkillRequirement.objects.get().application_id

    confirm_response = client.post(
        url,
        {
            "survivor": str(survivor.pk),
            "reason": "Merge",
            "action": "confirm",
            "preview_token": preview.token,
        },
    )

    assert confirm_response.status_code == 302
    assert not SkillConcept.objects.filter(pk=loser.pk).exists()
    assert SkillConceptMergeAudit.objects.count() == 1
    assert skill_catalog_issues() == ()


@pytest.mark.django_db
def test_candidate_cannot_reach_merge_administration() -> None:
    account = candidate()
    loser, _ = resolve_skill_label("Python")
    client = Client()
    client.force_login(account)

    response = client.get(reverse("admin:skills_skillconcept_merge", args=[loser.pk]))

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/admin/login/")
    assert SkillConcept.objects.filter(pk=loser.pk).exists()
