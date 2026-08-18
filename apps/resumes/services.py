from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import Account
from apps.applications.models import ApplicationSkillRequirement, JobApplication
from apps.profiles.models import (
    CandidateProfile,
    ExperienceSkill,
    ProfileSkill,
    ProjectSkill,
)
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
from apps.skills.models import SkillConcept

SECTION_ORDER = (
    ResumeSection.Kind.SUMMARY,
    ResumeSection.Kind.SKILLS,
    ResumeSection.Kind.EXPERIENCE,
    ResumeSection.Kind.PROJECTS,
    ResumeSection.Kind.EDUCATION,
    ResumeSection.Kind.LANGUAGES,
)


@dataclass(frozen=True)
class ResumeDocument:
    resume: Resume
    profile: CandidateProfile
    header: dict[str, Any]
    sections: tuple[ResumeSection, ...]
    experiences: tuple[dict[str, Any], ...]
    projects: tuple[dict[str, Any], ...]
    educations: tuple[dict[str, Any], ...]
    languages: tuple[dict[str, Any], ...]
    skills: tuple[dict[str, Any], ...]


def _source_skills(
    profile: CandidateProfile,
) -> tuple[list[ProfileSkill], list[ExperienceSkill], list[ProjectSkill]]:
    profile_skills = list(
        profile.profile_skills.select_related("concept").order_by("position", "id")
    )
    experience_skills = list(
        ExperienceSkill.objects.filter(experience__profile=profile)
        .select_related("concept", "experience")
        .order_by("experience__position", "experience__id", "position", "id")
    )
    project_skills = list(
        ProjectSkill.objects.filter(project__profile=profile)
        .select_related("concept", "project")
        .order_by("project__position", "project__id", "position", "id")
    )
    return profile_skills, experience_skills, project_skills


def _skill_initialization_order(
    application: JobApplication,
    profile: CandidateProfile,
) -> list[int]:
    profile_skills, experience_skills, project_skills = _source_skills(profile)
    occurrence: dict[int, int] = {}
    evidence: dict[int, int] = {}
    concepts: set[int] = set()

    def add_skill(index: int, skill: Any, *, is_evidence: bool) -> None:
        concepts.add(skill.concept_id)
        occurrence.setdefault(skill.concept_id, index)
        if is_evidence:
            evidence[skill.concept_id] = evidence.get(skill.concept_id, 0) + 1

    index = 0
    for profile_skill in profile_skills:
        add_skill(index, profile_skill, is_evidence=False)
        index += 1
    for experience_skill in experience_skills:
        add_skill(index, experience_skill, is_evidence=True)
        index += 1
    for project_skill in project_skills:
        add_skill(index, project_skill, is_evidence=True)
        index += 1

    classifications: dict[int, int] = {}
    for requirement in application.skill_requirements.all():
        rank = (
            0
            if requirement.classification == ApplicationSkillRequirement.Classification.REQUIRED
            else 1
        )
        classifications[requirement.concept_id] = min(
            classifications.get(requirement.concept_id, 2), rank
        )

    return sorted(
        concepts,
        key=lambda concept_id: (
            classifications.get(concept_id, 2),
            -evidence.get(concept_id, 0) if concept_id in classifications else 0,
            occurrence[concept_id],
            concept_id,
        ),
    )


def _initialize_resume(
    resume: Resume,
    application: JobApplication,
    profile: CandidateProfile,
) -> None:
    ResumeSection.objects.bulk_create(
        [
            ResumeSection(resume=resume, kind=kind, position=position)
            for position, kind in enumerate(SECTION_ORDER)
        ]
    )

    requirements = set(application.skill_requirements.values_list("concept_id", flat=True))
    relevant_experience_ids = set(
        ExperienceSkill.objects.filter(
            experience__profile=profile,
            concept_id__in=requirements,
        ).values_list("experience_id", flat=True)
    )
    experiences = list(profile.experiences.order_by("position", "id"))
    experiences.sort(
        key=lambda experience: (
            experience.pk not in relevant_experience_ids,
            experience.position,
            experience.pk,
        )
    )
    ResumeExperience.objects.bulk_create(
        [
            ResumeExperience(
                resume=resume,
                experience=experience,
                included=True,
                is_relevant=experience.pk in relevant_experience_ids,
                position=position,
            )
            for position, experience in enumerate(experiences)
        ]
    )

    relevant_project_ids = set(
        ProjectSkill.objects.filter(
            project__profile=profile,
            concept_id__in=requirements,
        ).values_list("project_id", flat=True)
    )
    projects = list(profile.projects.order_by("position", "id"))
    projects.sort(
        key=lambda project: (project.pk not in relevant_project_ids, project.position, project.pk)
    )
    ResumeProject.objects.bulk_create(
        [
            ResumeProject(
                resume=resume,
                project=project,
                included=True,
                position=position,
            )
            for position, project in enumerate(projects)
        ]
    )

    educations = list(profile.educations.order_by("position", "id"))
    ResumeEducation.objects.bulk_create(
        [
            ResumeEducation(resume=resume, education=education, position=position)
            for position, education in enumerate(educations)
        ]
    )
    languages = list(profile.languages.order_by("position", "id"))
    ResumeLanguage.objects.bulk_create(
        [
            ResumeLanguage(resume=resume, language=language, position=position)
            for position, language in enumerate(languages)
        ]
    )

    concept_ids = _skill_initialization_order(application, profile)
    concepts = SkillConcept.objects.in_bulk(concept_ids)
    ResumeSkill.objects.bulk_create(
        [
            ResumeSkill(resume=resume, concept=concepts[concept_id], position=position)
            for position, concept_id in enumerate(concept_ids)
        ]
    )


@transaction.atomic
def open_resume(*, account: Account, application_id: int) -> tuple[Resume, bool]:
    application = JobApplication.objects.select_for_update().get(
        pk=application_id,
        account=account,
    )
    profile = CandidateProfile.objects.get(account=account)
    resume = Resume.objects.filter(application=application).first()
    if resume is not None:
        return Resume.objects.select_for_update().get(pk=resume.pk), False

    resume = Resume.objects.create(application=application)
    _initialize_resume(resume, application, profile)
    return resume, True


def _effective(override: Any, source: Any) -> Any:
    return source if override is None else override


def _labels_by_concept(profile: CandidateProfile) -> dict[int, str]:
    profile_skills, experience_skills, project_skills = _source_skills(profile)
    labels: dict[int, str] = {}
    for skills in (profile_skills, experience_skills, project_skills):
        for skill in skills:
            labels.setdefault(skill.concept_id, skill.label)
    return labels


def _render_experiences(resume: Resume, profile: CandidateProfile) -> tuple[dict[str, Any], ...]:
    items: list[dict[str, Any]] = []
    overlays = resume.experiences.select_related("experience").prefetch_related("highlights").all()
    for overlay in overlays:
        source = overlay.experience
        highlight_overlays = {
            item.highlight_id: item for item in overlay.highlights.select_related("highlight").all()
        }
        highlights: list[dict[str, Any]] = []
        for highlight in source.highlights.order_by("position", "id"):
            child = highlight_overlays.get(highlight.pk)
            if child is not None and not child.included:
                continue
            highlights.append(
                {
                    "source": highlight,
                    "text": _effective(child.text_override if child else None, highlight.text),
                    "position": child.position
                    if child and child.position is not None
                    else highlight.position,
                }
            )
        highlights.sort(key=lambda item: (item["position"], item["source"].pk))
        items.append(
            {
                "source": source,
                "included": overlay.included,
                "is_relevant": overlay.is_relevant,
                "position": overlay.position,
                "role": _effective(overlay.role_override, source.role),
                "organization": _effective(overlay.organization_override, source.organization),
                "location": _effective(overlay.location_override, source.location),
                "start_date": _effective(overlay.start_date_override, source.start_date),
                "end_date": _effective(overlay.end_date_override, source.end_date),
                "description": _effective(overlay.description_override, source.description),
                "highlights": highlights,
            }
        )
    return tuple(item for item in items if item["included"])


def build_resume_document(*, account: Account, resume: Resume) -> ResumeDocument:
    if resume.application.account_id != account.pk:
        raise Resume.DoesNotExist
    application = resume.application
    profile = CandidateProfile.objects.get(account=application.account)
    header = {
        "contact_email": _effective(resume.contact_email_override, profile.contact_email),
        "full_name": _effective(resume.full_name_override, profile.full_name),
        "professional_title": _effective(
            resume.professional_title_override, profile.professional_title
        ),
        "professional_summary": _effective(
            resume.professional_summary_override, profile.professional_summary
        ),
        "phone_number": _effective(resume.phone_number_override, profile.phone_number),
        "location": _effective(resume.location_override, profile.location),
        "linkedin_url": _effective(resume.linkedin_url_override, profile.linkedin_url),
        "portfolio_url": _effective(resume.portfolio_url_override, profile.portfolio_url),
    }
    experiences = _render_experiences(resume, profile)
    projects = tuple(
        {
            "source": overlay.project,
            "included": overlay.included,
            "position": overlay.position,
            "name": _effective(overlay.name_override, overlay.project.name),
            "description": _effective(overlay.description_override, overlay.project.description),
            "url": _effective(overlay.url_override, overlay.project.url),
        }
        for overlay in resume.projects.select_related("project").filter(included=True)
    )
    educations = tuple(
        {
            "source": overlay.education,
            "included": overlay.included,
            "position": overlay.position,
            "institution": _effective(overlay.institution_override, overlay.education.institution),
            "degree": _effective(overlay.degree_override, overlay.education.degree),
            "start_date": _effective(overlay.start_date_override, overlay.education.start_date),
            "end_date": _effective(overlay.end_date_override, overlay.education.end_date),
        }
        for overlay in resume.educations.select_related("education").filter(included=True)
    )
    languages = tuple(
        {
            "source": overlay.language,
            "included": overlay.included,
            "position": overlay.position,
            "name": _effective(overlay.name_override, overlay.language.name),
            "proficiency": _effective(overlay.proficiency_override, overlay.language.proficiency),
        }
        for overlay in resume.languages.select_related("language").filter(included=True)
    )
    labels = _labels_by_concept(profile)
    skills = tuple(
        {
            "source": overlay.concept,
            "included": overlay.included,
            "position": overlay.position,
            "label": _effective(
                overlay.label_override,
                labels.get(overlay.concept_id, ""),
            ),
        }
        for overlay in resume.skills.select_related("concept").filter(included=True)
    )
    return ResumeDocument(
        resume=resume,
        profile=profile,
        header=header,
        sections=tuple(resume.sections.all()),
        experiences=experiences,
        projects=projects,
        educations=educations,
        languages=languages,
        skills=skills,
    )


@transaction.atomic
def append_resume_skill(
    *,
    account: Account,
    concept_id: int,
    experience_id: int | None = None,
    project_id: int | None = None,
) -> None:
    resumes = Resume.objects.select_for_update().filter(application__account=account)
    for resume in resumes:
        if (
            experience_id is not None
            and not ResumeExperience.objects.filter(
                resume=resume,
                experience_id=experience_id,
                included=True,
            ).exists()
        ):
            continue
        if (
            project_id is not None
            and not ResumeProject.objects.filter(
                resume=resume,
                project_id=project_id,
                included=True,
            ).exists()
        ):
            continue
        if ResumeSkill.objects.filter(resume=resume, concept_id=concept_id).exists():
            continue
        last = resume.skills.order_by("-position", "-id").first()
        ResumeSkill.objects.create(
            resume=resume,
            concept_id=concept_id,
            position=last.position + 1 if last is not None else 0,
        )


@transaction.atomic
def remove_resume_skill_if_unreferenced(*, account: Account, concept_id: int) -> None:
    still_used = (
        ProfileSkill.objects.filter(profile__account=account, concept_id=concept_id).exists()
        or ExperienceSkill.objects.filter(
            experience__profile__account=account, concept_id=concept_id
        ).exists()
        or ProjectSkill.objects.filter(
            project__profile__account=account, concept_id=concept_id
        ).exists()
    )
    if not still_used:
        ResumeSkill.objects.filter(
            resume__application__account=account, concept_id=concept_id
        ).delete()


@transaction.atomic
def save_resume_draft(
    *,
    account: Account,
    application_id: int,
    values: dict[str, Any],
) -> Resume:
    resume = Resume.objects.select_for_update().get(
        application__pk=application_id,
        application__account=account,
    )
    profile = CandidateProfile.objects.get(account=account)
    submitted = set(values)

    for field in (
        "contact_email",
        "full_name",
        "professional_title",
        "professional_summary",
        "phone_number",
        "location",
        "linkedin_url",
        "portfolio_url",
    ):
        setattr(resume, f"{field}_override", values.get(field) or None)

    sections = list(resume.sections.all())
    for section in sections:
        field = f"section_{section.kind}_position"
        section.position = values[f"section_{section.kind}_position"]
        section.save(update_fields=["position"])

    def update_items(items: list[Any], prefix: str, fields: tuple[str, ...]) -> None:
        for item in items:
            item_prefix = f"{prefix}_{item.pk}"
            item.included = bool(values[f"{item_prefix}_included"])
            item.position = values[f"{item_prefix}_position"]
            for field in fields:
                field_name = f"{item_prefix}_{field}"
                setattr(item, field, values[field_name] or None)
            item.save()

    experience_items = list(resume.experiences.select_related("experience").all())
    if any(item.experience.profile_id != profile.pk for item in experience_items):
        raise ValidationError("Resume experience sources must belong to this Candidate Profile.")
    update_items(
        experience_items,
        "experience",
        (
            "role_override",
            "organization_override",
            "location_override",
            "start_date_override",
            "end_date_override",
            "description_override",
        ),
    )
    for item in experience_items:
        overlay_highlights = {
            highlight.highlight_id: highlight
            for highlight in item.highlights.select_related("highlight").all()
        }
        for highlight in item.experience.highlights.all():
            prefix = f"highlight_{item.pk}_{highlight.pk}"
            included_field = f"{prefix}_included"
            position_field = f"{prefix}_position"
            text_field = f"{prefix}_text_override"
            if not {included_field, position_field, text_field}.intersection(submitted):
                continue
            included = bool(values.get(included_field, True))
            position = values.get(position_field)
            text_override = values.get(text_field) or None
            existing = overlay_highlights.get(highlight.pk)
            is_default = included and position == highlight.position and text_override is None
            if is_default:
                if existing is not None:
                    existing.delete()
                continue
            if existing is None:
                ResumeExperienceHighlight.objects.create(
                    resume_experience=item,
                    highlight=highlight,
                    included=included,
                    position=position,
                    text_override=text_override,
                )
            else:
                existing.included = included
                existing.position = position
                existing.text_override = text_override
                existing.save()

    project_items = list(resume.projects.select_related("project").all())
    if any(item.project.profile_id != profile.pk for item in project_items):
        raise ValidationError("Resume project sources must belong to this Candidate Profile.")
    update_items(
        project_items, "project", ("name_override", "description_override", "url_override")
    )

    education_items = list(resume.educations.select_related("education").all())
    if any(item.education.profile_id != profile.pk for item in education_items):
        raise ValidationError("Resume education sources must belong to this Candidate Profile.")
    update_items(
        education_items,
        "education",
        ("institution_override", "degree_override", "start_date_override", "end_date_override"),
    )

    language_items = list(resume.languages.select_related("language").all())
    if any(item.language.profile_id != profile.pk for item in language_items):
        raise ValidationError("Resume language sources must belong to this Candidate Profile.")
    update_items(language_items, "language", ("name_override", "proficiency_override"))
    update_items(list(resume.skills.all()), "skill", ("label_override",))

    resume.full_clean(exclude=["application"])
    resume.save()
    return resume
