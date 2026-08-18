import pytest
from allauth.account.models import EmailAddress
from django.apps import apps
from django.db import IntegrityError, connection, transaction
from django.db.migrations.loader import MigrationLoader
from django.db.models.deletion import ProtectedError
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Account
from apps.applications.models import ApplicationSkillRequirement, Company, JobApplication
from apps.campaigns.models import Campaign
from apps.profiles.models import (
    CandidateProfile,
    Education,
    Experience,
    ExperienceSkill,
    Highlight,
    Language,
    ProfileSkill,
    Project,
    ProjectSkill,
)
from apps.profiles.services import create_experience_skill, delete_experience_skill
from apps.resumes.forms import ResumeDraftForm
from apps.resumes.models import (
    Resume,
    ResumeEducation,
    ResumeExperience,
    ResumeExperienceHighlight,
    ResumeLanguage,
    ResumeProject,
    ResumeSection,
    ResumeSkill,
)
from apps.resumes.services import open_resume
from apps.skills.models import SkillConcept

pytestmark = pytest.mark.integration


def verified_candidate(email: str) -> Account:
    account = Account.objects.create_user(email, "a-secure-password")
    EmailAddress.objects.create(user=account, email=account.email, primary=True, verified=True)
    profile = CandidateProfile.objects.create(
        account=account,
        full_name="Ada Lovelace",
        timezone="Europe/London",
    )
    Campaign.objects.create(
        account=account,
        weekly_target=5,
        monthly_target=20,
        timezone=profile.timezone,
    )
    return account


def application_for(account: Account) -> JobApplication:
    return JobApplication.objects.create(
        account=account,
        campaign=Campaign.objects.get(account=account),
        company=Company.objects.create(name="Example Careers"),
        role_title="Platform engineer",
        job_description="Build dependable internal systems.",
    )


def profile_sources(account: Account) -> dict[str, object]:
    profile = account.candidate_profile
    concept = SkillConcept.objects.create(canonical_name="Django")
    experience = Experience.objects.create(
        profile=profile,
        role="Engineer",
        organization="Example",
        location="London",
        start_date="2020-01-01",
        description="Built systems.",
    )
    highlight = Highlight.objects.create(experience=experience, text="Improved reliability.")
    education = Education.objects.create(
        profile=profile,
        institution="University",
        degree="Mathematics",
        start_date="2016-01-01",
    )
    project = Project.objects.create(profile=profile, name="Toolkit", description="A toolkit.")
    language = Language.objects.create(
        profile=profile,
        name="English",
        proficiency=Language.Proficiency.NATIVE,
    )
    profile_skill = ProfileSkill.objects.create(profile=profile, concept=concept, label="Django")
    experience_skill = ExperienceSkill.objects.create(
        experience=experience,
        concept=concept,
        label="Django",
    )
    project_skill = ProjectSkill.objects.create(project=project, concept=concept, label="Django")
    return {
        "concept": concept,
        "experience": experience,
        "highlight": highlight,
        "education": education,
        "project": project,
        "language": language,
        "profile_skill": profile_skill,
        "experience_skill": experience_skill,
        "project_skill": project_skill,
    }


def resume_for(account: Account) -> Resume:
    return Resume.objects.create(application=application_for(account))


@pytest.mark.django_db
def test_resume_app_is_registered_and_migration_depends_on_current_source_apps() -> None:
    assert apps.get_app_config("resumes").name == "apps.resumes"

    migration = MigrationLoader(connection=connection).get_migration("resumes", "0001_initial")

    assert (
        "applications",
        "0006_remove_applicationskillrequirement_application_skill_requirement_label_not_blank_and_more",
    ) in migration.dependencies
    assert ("profiles", "0013_candidateprofile_contact_email") in migration.dependencies
    assert ("skills", "0001_initial") in migration.dependencies


@pytest.mark.django_db
def test_resume_is_application_owned_optional_and_not_eagerly_created() -> None:
    owner = verified_candidate("resume-owner@example.com")
    other = verified_candidate("resume-other@example.com")
    application = application_for(owner)
    other_application = application_for(other)

    assert Resume.objects.count() == 0

    resume = Resume.objects.create(application=application)

    assert resume.application.account_id == owner.pk
    assert not hasattr(resume, "account_id")
    assert list(Resume.objects.filter(application__account=owner)) == [resume]
    assert not Resume.objects.filter(application=other_application).exists()


@pytest.mark.django_db
def test_resume_constraints_enforce_one_document_and_one_section_kind() -> None:
    account = verified_candidate("resume-constraints@example.com")
    resume = resume_for(account)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Resume.objects.create(application=resume.application)

    ResumeSection.objects.create(
        resume=resume,
        kind=ResumeSection.Kind.SUMMARY,
        position=0,
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ResumeSection.objects.create(
                resume=resume,
                kind=ResumeSection.Kind.SUMMARY,
                position=1,
            )


@pytest.mark.django_db
def test_resume_source_and_skill_constraints_allow_same_positions_but_not_duplicate_identity() -> (
    None
):
    account = verified_candidate("resume-identity@example.com")
    resume = resume_for(account)
    sources = profile_sources(account)
    experience = sources["experience"]
    education = sources["education"]
    project = sources["project"]
    language = sources["language"]
    concept = sources["concept"]

    ResumeExperience.objects.create(resume=resume, experience=experience, position=0)
    ResumeExperience.objects.create(
        resume=resume,
        experience=Experience.objects.create(
            profile=account.candidate_profile,
            role="Second engineer",
            organization="Example",
            location="London",
            start_date="2021-01-01",
        ),
        position=0,
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ResumeExperience.objects.create(resume=resume, experience=experience, position=0)

    ResumeEducation.objects.create(resume=resume, education=education, position=0)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ResumeEducation.objects.create(resume=resume, education=education, position=1)

    ResumeProject.objects.create(resume=resume, project=project, position=0)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ResumeProject.objects.create(resume=resume, project=project, position=1)

    ResumeLanguage.objects.create(resume=resume, language=language, position=0)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ResumeLanguage.objects.create(resume=resume, language=language, position=1)

    ResumeSkill.objects.create(resume=resume, concept=concept, position=0)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ResumeSkill.objects.create(resume=resume, concept=concept, position=1)


@pytest.mark.django_db
def test_resume_experience_highlight_is_unique_by_resume_experience_and_source() -> None:
    account = verified_candidate("resume-highlight@example.com")
    resume = resume_for(account)
    sources = profile_sources(account)
    resume_experience = ResumeExperience.objects.create(
        resume=resume,
        experience=sources["experience"],
        position=0,
    )
    highlight = sources["highlight"]

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ResumeExperienceHighlight.objects.create(
                resume_experience=resume_experience,
                highlight=highlight,
                text_override="",
            )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ResumeExperienceHighlight.objects.create(
                resume_experience=resume_experience,
                highlight=highlight,
                text_override=" ",
            )

    ResumeExperienceHighlight.objects.create(
        resume_experience=resume_experience,
        highlight=highlight,
        position=0,
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ResumeExperienceHighlight.objects.create(
                resume_experience=resume_experience,
                highlight=highlight,
            )


@pytest.mark.django_db
def test_source_rows_cascade_and_shared_skill_concepts_are_protected() -> None:
    account = verified_candidate("resume-deletion@example.com")
    resume = resume_for(account)
    sources = profile_sources(account)
    resume_experience = ResumeExperience.objects.create(
        resume=resume,
        experience=sources["experience"],
        position=0,
    )
    resume_highlight = ResumeExperienceHighlight.objects.create(
        resume_experience=resume_experience,
        highlight=sources["highlight"],
        position=0,
    )
    second_highlight = Highlight.objects.create(
        experience=sources["experience"],
        text="Reduced deployment time.",
    )
    second_resume_highlight = ResumeExperienceHighlight.objects.create(
        resume_experience=resume_experience,
        highlight=second_highlight,
        position=1,
    )
    resume_education = ResumeEducation.objects.create(
        resume=resume,
        education=sources["education"],
        position=0,
    )
    resume_project = ResumeProject.objects.create(
        resume=resume,
        project=sources["project"],
        position=0,
    )
    resume_language = ResumeLanguage.objects.create(
        resume=resume,
        language=sources["language"],
        position=0,
    )
    resume_skill = ResumeSkill.objects.create(resume=resume, concept=sources["concept"], position=0)

    sources["highlight"].delete()
    assert not ResumeExperienceHighlight.objects.filter(pk=resume_highlight.pk).exists()

    sources["experience"].delete()
    sources["education"].delete()
    sources["project"].delete()
    sources["language"].delete()

    assert not ResumeExperience.objects.filter(pk=resume_experience.pk).exists()
    assert not ResumeExperienceHighlight.objects.filter(pk=second_resume_highlight.pk).exists()
    assert not ResumeEducation.objects.filter(pk=resume_education.pk).exists()
    assert not ResumeProject.objects.filter(pk=resume_project.pk).exists()
    assert not ResumeLanguage.objects.filter(pk=resume_language.pk).exists()

    sources["profile_skill"].delete()
    sources["experience_skill"].delete()
    sources["project_skill"].delete()

    with pytest.raises(ProtectedError):
        sources["concept"].delete()

    assert ResumeSkill.objects.filter(pk=resume_skill.pk).exists()


@pytest.mark.django_db
def test_application_deletion_cascades_resume_and_requirement_but_not_profile_sources() -> None:
    account = verified_candidate("resume-application-delete@example.com")
    application = application_for(account)
    resume = Resume.objects.create(application=application)
    concept = SkillConcept.objects.create(canonical_name="Python")
    ApplicationSkillRequirement.objects.create(
        application=application,
        concept=concept,
        label="Python",
        classification=ApplicationSkillRequirement.Classification.REQUIRED,
    )
    profile = account.candidate_profile
    application_id = application.pk

    application.delete()

    assert not Resume.objects.filter(pk=resume.pk).exists()
    assert not ApplicationSkillRequirement.objects.filter(application_id=application_id).exists()
    assert CandidateProfile.objects.filter(pk=profile.pk).exists()
    assert SkillConcept.objects.filter(pk=concept.pk).exists()


@pytest.mark.django_db
def test_open_resume_initializes_sections_membership_and_deterministic_skills_once() -> None:
    account = verified_candidate("resume-initialize@example.com")
    application = application_for(account)
    profile = account.candidate_profile
    required_high_evidence = SkillConcept.objects.create(canonical_name="Python")
    required_low_evidence = SkillConcept.objects.create(canonical_name="Django")
    preferred = SkillConcept.objects.create(canonical_name="PostgreSQL")
    unmatched = SkillConcept.objects.create(canonical_name="Rust")
    ApplicationSkillRequirement.objects.create(
        application=application,
        concept=required_high_evidence,
        label="Python",
        classification=ApplicationSkillRequirement.Classification.REQUIRED,
    )
    ApplicationSkillRequirement.objects.create(
        application=application,
        concept=required_low_evidence,
        label="Django",
        classification=ApplicationSkillRequirement.Classification.REQUIRED,
    )
    ApplicationSkillRequirement.objects.create(
        application=application,
        concept=preferred,
        label="PostgreSQL",
        classification=ApplicationSkillRequirement.Classification.PREFERRED,
    )
    first_experience = Experience.objects.create(
        profile=profile,
        role="First",
        organization="Example",
        location="London",
        start_date="2020-01-01",
    )
    second_experience = Experience.objects.create(
        profile=profile,
        role="Second",
        organization="Example",
        location="London",
        start_date="2021-01-01",
    )
    ExperienceSkill.objects.create(
        experience=first_experience, concept=required_high_evidence, label="Python"
    )
    ExperienceSkill.objects.create(experience=first_experience, concept=preferred, label="Postgres")
    ExperienceSkill.objects.create(
        experience=second_experience, concept=required_high_evidence, label="Python"
    )
    ExperienceSkill.objects.create(
        experience=second_experience, concept=required_low_evidence, label="Django"
    )
    first_project = Project.objects.create(profile=profile, name="First project")
    second_project = Project.objects.create(profile=profile, name="Second project")
    ProjectSkill.objects.create(project=first_project, concept=unmatched, label="Rust")
    ProjectSkill.objects.create(
        project=second_project, concept=required_low_evidence, label="Django"
    )
    ProfileSkill.objects.create(profile=profile, concept=preferred, label="SQL")

    resume, created = open_resume(account=account, application_id=application.pk)

    assert created is True
    assert list(resume.sections.values_list("kind", flat=True)) == [
        ResumeSection.Kind.SUMMARY,
        ResumeSection.Kind.SKILLS,
        ResumeSection.Kind.EXPERIENCE,
        ResumeSection.Kind.PROJECTS,
        ResumeSection.Kind.EDUCATION,
        ResumeSection.Kind.LANGUAGES,
    ]
    assert list(resume.experiences.values_list("experience_id", flat=True)) == [
        first_experience.pk,
        second_experience.pk,
    ]
    assert list(resume.projects.values_list("project_id", flat=True)) == [
        second_project.pk,
        first_project.pk,
    ]
    assert list(resume.skills.values_list("concept_id", flat=True)) == [
        required_high_evidence.pk,
        required_low_evidence.pk,
        preferred.pk,
        unmatched.pk,
    ]
    assert list(resume.skills.values_list("position", flat=True)) == [0, 1, 2, 3]

    ResumeSection.objects.filter(resume=resume, kind=ResumeSection.Kind.SKILLS).update(position=99)
    reopened, reopened_created = open_resume(account=account, application_id=application.pk)

    assert reopened_created is False
    assert reopened.pk == resume.pk
    assert reopened.sections.get(kind=ResumeSection.Kind.SKILLS).position == 99


@pytest.mark.django_db
def test_resume_detail_is_account_scoped_and_renders_live_profile_header() -> None:
    owner = verified_candidate("resume-http-owner@example.com")
    intruder = verified_candidate("resume-http-intruder@example.com")
    application = application_for(owner)
    owner.candidate_profile.contact_email = "ada-resume@example.com"
    owner.candidate_profile.save(update_fields=["contact_email", "updated_at"])
    client = Client()
    client.force_login(owner)

    response = client.get(reverse("resume_detail", args=[application.pk]))

    assert response.status_code == 200
    assert Resume.objects.filter(application=application).count() == 1
    assert b"Ada Lovelace" in response.content
    assert b"ada-resume@example.com" in response.content
    assert b"Resume" in response.content

    resume = Resume.objects.get(application=application)
    draft_form = ResumeDraftForm(resume=resume)
    draft = {
        name: field.initial
        for name, field in draft_form.fields.items()
        if field.required and field.initial is not None
    }
    draft["full_name"] = "Tailored Ada Lovelace"
    saved = client.post(
        reverse("resume_save", args=[application.pk]),
        draft,
    )

    assert saved.status_code == 302
    assert saved.headers["Location"] == reverse("resume_detail", args=[application.pk])
    assert Resume.objects.get(application=application).full_name_override == "Tailored Ada Lovelace"
    assert owner.candidate_profile.full_name == "Ada Lovelace"
    assert (
        b"Tailored Ada Lovelace"
        in client.get(reverse("resume_detail", args=[application.pk])).content
    )

    client.force_login(intruder)
    private_response = client.get(reverse("resume_detail", args=[application.pk]))

    assert private_response.status_code == 404


@pytest.mark.django_db
def test_profile_skill_service_appends_and_removes_resume_skill_at_final_source() -> None:
    account = verified_candidate("resume-skill-integration@example.com")
    application = application_for(account)
    experience = Experience.objects.create(
        profile=account.candidate_profile,
        role="Engineer",
        organization="Example",
        location="London",
        start_date="2020-01-01",
    )
    resume, _created = open_resume(account=account, application_id=application.pk)

    created = create_experience_skill(account=account, experience_id=experience.pk, label="Python")

    assert ResumeSkill.objects.filter(resume=resume, concept=created.concept).count() == 1
    delete_experience_skill(account=account, experience_skill_id=created.pk)
    assert not ResumeSkill.objects.filter(resume=resume, concept=created.concept).exists()
