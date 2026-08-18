from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import Account
from apps.applications.models import ApplicationSkillRequirement, JobApplication
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


def _contiguous(values: list[int], label: str) -> None:
    if sorted(values) != list(range(len(values))):
        raise ValidationError(f"{label} positions must be contiguous and unique.")


def _source_map(model: Any, *, ids: set[int], profile: CandidateProfile) -> dict[int, Any]:
    sources = model.objects.filter(pk__in=ids, profile=profile)
    source_map = {source.pk: source for source in sources}
    if set(source_map) != ids:
        raise ValidationError("All submitted source records must belong to this Candidate Profile.")
    return source_map


def _validate_unique_ids(rows: list[dict[str, Any]], key: str) -> set[int]:
    ids = [row[key] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValidationError("Each source identity may appear only once.")
    return set(ids)


def _require_complete_ids(
    rows: list[dict[str, Any]], *, key: str, expected: set[int], label: str
) -> set[int]:
    ids = _validate_unique_ids(rows, key)
    if ids != expected:
        raise ValidationError(f"Complete {label} source membership must be submitted.")
    return ids


def _save_canonical_resume_draft(
    *,
    account: Account,
    application_id: int,
    values: dict[str, Any],
) -> Resume:
    application = JobApplication.objects.select_for_update().get(
        pk=application_id,
        account=account,
    )
    resume = Resume.objects.select_for_update().get(application=application)
    profile = CandidateProfile.objects.get(account=account)

    expected_header = {
        "contact_email",
        "full_name",
        "professional_title",
        "professional_summary",
        "phone_number",
        "location",
        "linkedin_url",
        "portfolio_url",
    }
    header = values.get("header")
    if not isinstance(header, dict) or not expected_header.issubset(header):
        raise ValidationError("A complete Resume header is required.")
    for field in expected_header:
        setattr(resume, f"{field}_override", header[field] or None)

    sections = values.get("sections")
    if not isinstance(sections, list):
        raise ValidationError("A complete Resume section list is required.")
    section_kinds = [section.get("kind") for section in sections]
    if set(section_kinds) != set(SECTION_ORDER) or len(section_kinds) != len(SECTION_ORDER):
        raise ValidationError("Resume sections must contain each section exactly once.")
    _contiguous([section["position"] for section in sections], "Section")
    existing_sections = {section.kind: section for section in resume.sections.all()}
    for section_data in sections:
        section = existing_sections.get(section_data["kind"])
        if section is None:
            section = ResumeSection.objects.create(resume=resume, kind=section_data["kind"])
        section.position = section_data["position"]
        section.save(update_fields=["position"])

    raw_rows = [
        values.get("experiences"),
        values.get("projects"),
        values.get("educations"),
        values.get("languages"),
        values.get("skills"),
    ]
    if not all(isinstance(rows, list) for rows in raw_rows):
        raise ValidationError("A complete Resume membership draft is required.")
    experience_rows = cast(list[dict[str, Any]], raw_rows[0])
    project_rows = cast(list[dict[str, Any]], raw_rows[1])
    education_rows = cast(list[dict[str, Any]], raw_rows[2])
    language_rows = cast(list[dict[str, Any]], raw_rows[3])
    skill_rows = cast(list[dict[str, Any]], raw_rows[4])

    experience_ids = _require_complete_ids(
        experience_rows,
        key="source_id",
        expected=set(profile.experiences.values_list("pk", flat=True)),
        label="Experience",
    )
    project_ids = _require_complete_ids(
        project_rows,
        key="source_id",
        expected=set(profile.projects.values_list("pk", flat=True)),
        label="Project",
    )
    education_ids = _require_complete_ids(
        education_rows,
        key="source_id",
        expected=set(profile.educations.values_list("pk", flat=True)),
        label="Education",
    )
    language_ids = _require_complete_ids(
        language_rows,
        key="source_id",
        expected=set(profile.languages.values_list("pk", flat=True)),
        label="Language",
    )
    experience_sources = _source_map(Experience, ids=experience_ids, profile=profile)
    project_sources = _source_map(Project, ids=project_ids, profile=profile)
    education_sources = _source_map(Education, ids=education_ids, profile=profile)
    language_sources = _source_map(Language, ids=language_ids, profile=profile)

    for rows, label in (
        (experience_rows, "Experience"),
        (project_rows, "Project"),
        (education_rows, "Education"),
        (language_rows, "Language"),
    ):
        _contiguous([row["position"] for row in rows if row.get("included")], label)

    def update_overlay(
        *,
        model: Any,
        source_field: str,
        source_map: dict[int, Any],
        rows: list[dict[str, Any]],
        fields: tuple[str, ...],
    ) -> dict[int, Any]:
        overlays = {
            getattr(item, f"{source_field}_id"): item
            for item in model.objects.filter(resume=resume)
        }
        model.objects.filter(resume=resume).exclude(
            **{f"{source_field}_id__in": set(source_map)}
        ).delete()
        updated: dict[int, Any] = {}
        for row in rows:
            source_id = row["source_id"]
            item = overlays.get(source_id)
            if item is None:
                if not row.get("included"):
                    continue
                item = model(resume=resume, **{source_field: source_map[source_id]})
            item.included = bool(row.get("included"))
            item.position = row["position"] if item.included else 0
            for field in fields:
                setattr(item, field, row.get(field) or None if item.included else None)
            item.save()
            updated[source_id] = item
        return updated

    experience_overlays = update_overlay(
        model=ResumeExperience,
        source_field="experience",
        source_map=experience_sources,
        rows=experience_rows,
        fields=(
            "role_override",
            "organization_override",
            "location_override",
            "start_date_override",
            "end_date_override",
            "description_override",
        ),
    )
    update_overlay(
        model=ResumeProject,
        source_field="project",
        source_map=project_sources,
        rows=project_rows,
        fields=("name_override", "description_override", "url_override"),
    )
    update_overlay(
        model=ResumeEducation,
        source_field="education",
        source_map=education_sources,
        rows=education_rows,
        fields=(
            "institution_override",
            "degree_override",
            "start_date_override",
            "end_date_override",
        ),
    )
    update_overlay(
        model=ResumeLanguage,
        source_field="language",
        source_map=language_sources,
        rows=language_rows,
        fields=("name_override", "proficiency_override"),
    )

    raw_highlight_rows = values.get("highlights")
    if not isinstance(raw_highlight_rows, list):
        raise ValidationError("A complete Resume highlight list is required.")
    highlight_rows = cast(list[dict[str, Any]], raw_highlight_rows)
    highlight_keys = [(row["experience_id"], row["source_id"]) for row in highlight_rows]
    if len(highlight_keys) != len(set(highlight_keys)):
        raise ValidationError("Each highlight identity may appear only once per experience.")
    expected_highlight_keys = set(
        Highlight.objects.filter(experience__profile=profile).values_list("experience_id", "pk")
    )
    if set(highlight_keys) != expected_highlight_keys:
        raise ValidationError("Complete Highlight source membership must be submitted.")
    experience_highlights: dict[int, dict[int, Highlight]] = {}
    for experience in experience_sources.values():
        experience_highlights[experience.pk] = {
            highlight.pk: highlight for highlight in experience.highlights.all()
        }
    for row in highlight_rows:
        source_highlight = experience_highlights.get(row["experience_id"], {}).get(row["source_id"])
        if source_highlight is None:
            raise ValidationError("Submitted highlights must belong to their submitted Experience.")
    submitted_highlight_keys = set(highlight_keys)
    for overlay in ResumeExperienceHighlight.objects.filter(resume_experience__resume=resume):
        key = (overlay.resume_experience.experience_id, overlay.highlight_id)
        if key not in submitted_highlight_keys:
            overlay.delete()
    grouped_positions: dict[int, list[int]] = {}
    for row in highlight_rows:
        if row.get("included"):
            grouped_positions.setdefault(row["experience_id"], []).append(row["position"])
    for positions in grouped_positions.values():
        _contiguous(positions, "Highlight")
    for row in highlight_rows:
        parent = experience_overlays.get(row["experience_id"])
        if parent is None or not parent.included:
            if parent is not None:
                ResumeExperienceHighlight.objects.filter(resume_experience=parent).delete()
            continue
        existing = ResumeExperienceHighlight.objects.filter(
            resume_experience=parent,
            highlight_id=row["source_id"],
        ).first()
        included = bool(row.get("included")) and parent.included
        position = row.get("position") if included else None
        text_override = row.get("text_override") or None if included else None
        source = experience_highlights[row["experience_id"]][row["source_id"]]
        is_default = included and position == source.position and text_override is None
        if is_default:
            if existing is not None:
                existing.delete()
        elif existing is None:
            ResumeExperienceHighlight.objects.create(
                resume_experience=parent,
                highlight=source,
                included=included,
                position=position,
                text_override=text_override,
            )
        else:
            existing.included = included
            existing.position = position
            existing.text_override = text_override
            existing.save()

    available_concepts = (
        set(ProfileSkill.objects.filter(profile=profile).values_list("concept_id", flat=True))
        | set(
            ExperienceSkill.objects.filter(experience__profile=profile).values_list(
                "concept_id", flat=True
            )
        )
        | set(
            ProjectSkill.objects.filter(project__profile=profile).values_list(
                "concept_id", flat=True
            )
        )
    )
    concept_ids = _require_complete_ids(
        skill_rows,
        key="source_id",
        expected=available_concepts,
        label="Skill",
    )
    if not concept_ids.issubset(available_concepts):
        raise ValidationError("Submitted skills must belong to this Candidate Profile.")
    _contiguous([row["position"] for row in skill_rows if row.get("included")], "Skill")
    skill_overlays = {item.concept_id: item for item in resume.skills.all()}
    ResumeSkill.objects.filter(resume=resume).exclude(concept_id__in=concept_ids).delete()
    for row in skill_rows:
        item = skill_overlays.get(row["source_id"])
        if item is None:
            if not row.get("included"):
                continue
            item = ResumeSkill.objects.create(resume=resume, concept_id=row["source_id"])
        item.included = bool(row.get("included"))
        item.position = row["position"] if item.included else 0
        item.label_override = row.get("label_override") or None if item.included else None
        item.save()

    resume.full_clean(exclude=["application"])
    resume.save()
    return resume


@transaction.atomic
def save_resume_draft(
    *,
    account: Account,
    application_id: int,
    values: dict[str, Any],
) -> Resume:
    return _save_canonical_resume_draft(
        account=account,
        application_id=application_id,
        values=values,
    )
