from __future__ import annotations

from time import sleep

from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError, connection, transaction

from apps.skills.models import (
    SkillAlias,
    SkillConcept,
    clean_skill_label,
    normalize_skill_label,
)


def _resolved_skill_label(normalized_value: str) -> SkillAlias | None:
    return (
        SkillAlias.objects.select_related("concept")
        .filter(normalized_value=normalized_value)
        .first()
    )


def _resolved_skill_concept(normalized_value: str) -> SkillConcept | None:
    return SkillConcept.objects.filter(canonical_key=normalized_value).first()


@transaction.atomic
def _resolve_skill_label(display_name: str, normalized_value: str) -> tuple[SkillConcept, bool]:
    match = _resolved_skill_label(normalized_value)
    if match:
        return match.concept, False

    canonical_match = _resolved_skill_concept(normalized_value)
    if canonical_match:
        return canonical_match, False

    return SkillConcept.objects.create(canonical_name=display_name), True


def resolve_skill_label(label: str) -> tuple[SkillConcept, bool]:
    """Resolve an exact public skill label or create its shared concept."""
    display_name = clean_skill_label(label)
    normalized_value = normalize_skill_label(display_name)
    for attempt in range(5):
        try:
            return _resolve_skill_label(display_name, normalized_value)
        except IntegrityError:
            # A competing transaction has committed the winning namespace row.
            match = _resolved_skill_label(normalized_value)
            if match:
                return match.concept, False
            canonical_match = _resolved_skill_concept(normalized_value)
            if canonical_match:
                return canonical_match, False
            raise
        except OperationalError as error:
            if connection.vendor != "sqlite" or "locked" not in str(error).lower():
                raise
            if attempt == 4:
                raise
            sleep(0.05 * (2**attempt))

    raise RuntimeError("Skill label resolution did not complete.")


@transaction.atomic
def rename_skill_concept(*, concept: SkillConcept, canonical_name: str) -> SkillConcept:
    """Change a canonical label without changing the concept identity."""
    display_name = clean_skill_label(canonical_name)
    normalized_value = normalize_skill_label(display_name)
    locked_concept = SkillConcept.objects.select_for_update().get(pk=concept.pk)
    current_canonical = SkillAlias.objects.select_for_update().get(
        concept=locked_concept,
        is_canonical=True,
    )
    target = (
        SkillAlias.objects.select_for_update().filter(normalized_value=normalized_value).first()
    )
    if target and target.concept_id != locked_concept.pk:
        raise ValidationError("This label already belongs to another skill concept.")

    if target and target.pk != current_canonical.pk:
        current_canonical.is_canonical = False
        current_canonical.save(update_fields=["is_canonical"])
        target.is_canonical = True
        target.display_name = display_name
        target.save(update_fields=["display_name", "normalized_value", "is_canonical"])
    elif not target:
        current_canonical.is_canonical = False
        current_canonical.save(update_fields=["is_canonical"])
        SkillAlias.objects.create(
            concept=locked_concept,
            display_name=display_name,
            is_canonical=True,
        )

    locked_concept.canonical_name = display_name
    locked_concept.save(update_fields=["canonical_name", "canonical_key", "updated_at"])
    return locked_concept
