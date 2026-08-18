from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.accounts.models import Account
from apps.profiles.models import (
    CandidateProfile,
    Experience,
    ExperienceSkill,
    ProfileSkill,
    Project,
    ProjectSkill,
)
from apps.skills.models import clean_skill_label
from apps.skills.services import resolve_skill_label


@transaction.atomic
def create_profile_skill(*, account: Account, label: str) -> ProfileSkill:
    profile = CandidateProfile.objects.select_for_update().get(account=account)
    display_label = clean_skill_label(label)
    concept, _ = resolve_skill_label(display_label)
    if ProfileSkill.objects.filter(profile=profile, concept=concept).exists():
        raise ValidationError("This skill is already in your profile.")
    try:
        with transaction.atomic():
            return ProfileSkill.objects.create(
                profile=profile,
                concept=concept,
                label=display_label,
                position=profile.profile_skills.count(),
            )
    except IntegrityError as error:
        raise ValidationError("This skill is already in your profile.") from error


@transaction.atomic
def delete_profile_skill(*, account: Account, skill_id: int) -> None:
    profile_id = (
        ProfileSkill.objects.filter(
            pk=skill_id,
            profile__account=account,
        )
        .values_list("profile_id", flat=True)
        .get()
    )
    profile = CandidateProfile.objects.select_for_update().get(pk=profile_id, account=account)
    skill = ProfileSkill.objects.select_for_update().get(pk=skill_id, profile=profile)
    skill.delete()
    for position, remaining_skill in enumerate(profile.profile_skills.order_by("position", "id")):
        if remaining_skill.position != position:
            ProfileSkill.objects.filter(pk=remaining_skill.pk).update(position=position)


@transaction.atomic
def create_experience_skill(*, account: Account, experience_id: int, label: str) -> ExperienceSkill:
    experience = Experience.objects.select_for_update().get(
        pk=experience_id,
        profile__account=account,
    )
    display_label = clean_skill_label(label)
    concept, _ = resolve_skill_label(display_label)
    if ExperienceSkill.objects.filter(experience=experience, concept=concept).exists():
        raise ValidationError("This skill is already used in this experience.")
    try:
        with transaction.atomic():
            return ExperienceSkill.objects.create(
                experience=experience,
                concept=concept,
                label=display_label,
                position=experience.experience_skills.count(),
            )
    except IntegrityError as error:
        raise ValidationError("This skill is already used in this experience.") from error


@transaction.atomic
def delete_experience_skill(*, account: Account, experience_skill_id: int) -> None:
    experience_id = (
        ExperienceSkill.objects.filter(
            pk=experience_skill_id,
            experience__profile__account=account,
        )
        .values_list("experience_id", flat=True)
        .get()
    )
    experience = Experience.objects.select_for_update().get(
        pk=experience_id,
        profile__account=account,
    )
    experience_skill = ExperienceSkill.objects.select_for_update().get(
        pk=experience_skill_id,
        experience=experience,
    )
    experience_skill.delete()
    for position, remaining_skill in enumerate(
        experience.experience_skills.order_by("position", "id")
    ):
        if remaining_skill.position != position:
            ExperienceSkill.objects.filter(pk=remaining_skill.pk).update(position=position)


@transaction.atomic
def create_project_skill(*, account: Account, project_id: int, label: str) -> ProjectSkill:
    project = Project.objects.select_for_update().get(
        pk=project_id,
        profile__account=account,
    )
    display_label = clean_skill_label(label)
    concept, _ = resolve_skill_label(display_label)
    if ProjectSkill.objects.filter(project=project, concept=concept).exists():
        raise ValidationError("This skill is already used in this project.")
    try:
        with transaction.atomic():
            return ProjectSkill.objects.create(
                project=project,
                concept=concept,
                label=display_label,
                position=project.project_skills.count(),
            )
    except IntegrityError as error:
        raise ValidationError("This skill is already used in this project.") from error


@transaction.atomic
def delete_project_skill(*, account: Account, project_skill_id: int) -> None:
    project_id = (
        ProjectSkill.objects.filter(
            pk=project_skill_id,
            project__profile__account=account,
        )
        .values_list("project_id", flat=True)
        .get()
    )
    project = Project.objects.select_for_update().get(
        pk=project_id,
        profile__account=account,
    )
    project_skill = ProjectSkill.objects.select_for_update().get(
        pk=project_skill_id,
        project=project,
    )
    project_skill.delete()
    for position, remaining_skill in enumerate(project.project_skills.order_by("position", "id")):
        if remaining_skill.position != position:
            ProjectSkill.objects.filter(pk=remaining_skill.pk).update(position=position)
