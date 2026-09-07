from __future__ import annotations

from collections.abc import Callable
from time import sleep
from typing import TypeVar

from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError, connection, transaction

from apps.skills.models import (
    SkillAlias,
    SkillConcept,
    catalog_operation,
    clean_skill_label,
    normalize_skill_label,
)

T = TypeVar("T")


def _resolved_skill_label(normalized_value: str) -> SkillAlias | None:
    return (
        SkillAlias.objects.select_related("concept")
        .filter(normalized_value=normalized_value)
        .first()
    )


def _resolved_skill_concept(normalized_value: str) -> SkillConcept | None:
    return SkillConcept.objects.filter(canonical_key=normalized_value).first()


def _canonical_skill_alias(concept: SkillConcept) -> SkillAlias:
    return SkillAlias.objects.get(concept=concept, is_canonical=True)


@transaction.atomic
def _resolve_skill_label(display_name: str, normalized_value: str) -> tuple[SkillConcept, bool]:
    match = _resolved_skill_label(normalized_value)
    if match:
        return match.concept, False

    canonical_match = _resolved_skill_concept(normalized_value)
    if canonical_match:
        return canonical_match, False

    return SkillConcept.objects.create(canonical_name=display_name), True


@transaction.atomic
def _resolve_skill_alias(display_name: str, normalized_value: str) -> tuple[SkillAlias, bool]:
    match = _resolved_skill_label(normalized_value)
    if match:
        return match, False

    canonical_match = _resolved_skill_concept(normalized_value)
    if canonical_match:
        return _canonical_skill_alias(canonical_match), False

    concept = SkillConcept.objects.create(canonical_name=display_name)
    return _canonical_skill_alias(concept), True


def _retry_skill_resolution[T](
    operation: Callable[[], T],
    recovered: Callable[[], T | None],
) -> T:
    """Run a namespace-mutating resolution, retrying a lost namespace race."""
    for attempt in range(5):
        try:
            return operation()
        except IntegrityError:
            # A competing transaction has committed the winning namespace row.
            recovered_result = recovered()
            if recovered_result is not None:
                return recovered_result
            raise
        except OperationalError as error:
            if connection.vendor != "sqlite" or "locked" not in str(error).lower():
                raise
            if attempt == 4:
                raise
            sleep(0.05 * (2**attempt))

    raise RuntimeError("Skill wording resolution did not complete.")


def resolve_skill_label(label: str) -> tuple[SkillConcept, bool]:
    """Resolve an exact public skill label or create its shared concept."""
    display_name = clean_skill_label(label)
    normalized_value = normalize_skill_label(display_name)

    def recovered() -> tuple[SkillConcept, bool] | None:
        match = _resolved_skill_label(normalized_value)
        if match:
            return match.concept, False
        canonical_match = _resolved_skill_concept(normalized_value)
        if canonical_match:
            return canonical_match, False
        return None

    return _retry_skill_resolution(
        lambda: _resolve_skill_label(display_name, normalized_value),
        recovered,
    )


def resolve_skill_alias(label: str) -> tuple[SkillAlias, bool]:
    """Resolve public wording to its shared alias, creating the shared concept when unknown."""
    display_name = clean_skill_label(label)
    normalized_value = normalize_skill_label(display_name)

    def recovered() -> tuple[SkillAlias, bool] | None:
        match = _resolved_skill_label(normalized_value)
        if match:
            return match, False
        canonical_match = _resolved_skill_concept(normalized_value)
        if canonical_match:
            return _canonical_skill_alias(canonical_match), False
        return None

    return _retry_skill_resolution(
        lambda: _resolve_skill_alias(display_name, normalized_value),
        recovered,
    )


@transaction.atomic
def _create_skill_alias(
    concept: SkillConcept, display_name: str, normalized_value: str
) -> tuple[SkillAlias, bool]:
    locked_concept = SkillConcept.objects.select_for_update().get(pk=concept.pk)
    existing = _resolved_skill_label(normalized_value)
    if existing:
        if existing.concept_id != locked_concept.pk:
            raise ValidationError("This label already belongs to another skill concept.")
        return existing, False

    owner = _resolved_skill_concept(normalized_value)
    if owner and owner.pk != locked_concept.pk:
        raise ValidationError("This label already belongs to another skill concept.")

    with catalog_operation():
        alias = SkillAlias.objects.create(concept=locked_concept, display_name=display_name)
    return alias, True


def create_skill_alias(*, concept: SkillConcept, display_name: str) -> tuple[SkillAlias, bool]:
    """Create shared wording for one skill concept, reusing any identical alias."""
    cleaned = clean_skill_label(display_name)
    normalized_value = normalize_skill_label(cleaned)
    for attempt in range(5):
        try:
            return _create_skill_alias(concept, cleaned, normalized_value)
        except IntegrityError:
            # A competing transaction has committed the winning namespace row.
            existing = _resolved_skill_label(normalized_value)
            if existing and existing.concept_id == concept.pk:
                return existing, False
            if existing:
                raise ValidationError(
                    "This label already belongs to another skill concept."
                ) from None
            owner = _resolved_skill_concept(normalized_value)
            if owner and owner.pk != concept.pk:
                raise ValidationError(
                    "This label already belongs to another skill concept."
                ) from None
        except OperationalError as error:
            if connection.vendor != "sqlite" or "locked" not in str(error).lower():
                raise
            if attempt == 4:
                raise
            sleep(0.05 * (2**attempt))

    raise RuntimeError("Skill alias creation did not complete.")


@transaction.atomic
def _rename_skill_alias(alias_id: int, display_name: str, normalized_value: str) -> SkillAlias:
    locked_alias = SkillAlias.objects.select_for_update().select_related("concept").get(pk=alias_id)
    if locked_alias.is_canonical:
        raise ValidationError("Rename the skill concept instead of its canonical alias.")

    existing = (
        SkillAlias.objects.filter(normalized_value=normalized_value)
        .exclude(pk=locked_alias.pk)
        .select_related("concept")
        .first()
    )
    if existing:
        if existing.concept_id == locked_alias.concept_id:
            raise ValidationError("This label is already in the shared skill namespace.")
        raise ValidationError("This label already belongs to another skill concept.")

    owner = (
        SkillConcept.objects.filter(canonical_key=normalized_value)
        .exclude(pk=locked_alias.concept_id)
        .first()
    )
    if owner:
        raise ValidationError("This label already belongs to another skill concept.")

    with catalog_operation():
        locked_alias.display_name = display_name
        locked_alias.save(update_fields=["display_name", "normalized_value"])
    return locked_alias


def rename_skill_alias(*, alias: SkillAlias, display_name: str) -> SkillAlias:
    """Correct shared wording for one noncanonical skill alias."""
    cleaned = clean_skill_label(display_name)
    normalized_value = normalize_skill_label(cleaned)
    for attempt in range(5):
        try:
            return _rename_skill_alias(alias.pk, cleaned, normalized_value)
        except IntegrityError:
            # A competing transaction has committed the winning namespace row.
            existing = _resolved_skill_label(normalized_value)
            if existing and existing.pk == alias.pk:
                return existing
            raise ValidationError("This label is already in the shared skill namespace.") from None
        except OperationalError as error:
            if connection.vendor != "sqlite" or "locked" not in str(error).lower():
                raise
            if attempt == 4:
                raise
            sleep(0.05 * (2**attempt))

    raise RuntimeError("Skill alias rewording did not complete.")


def skill_catalog_issues() -> tuple[str, ...]:
    """Detect catalog drift that direct mutations can produce outside the domain operations."""
    issues: list[str] = []
    concepts = {concept.pk: concept for concept in SkillConcept.objects.all()}
    aliases = list(SkillAlias.objects.all())

    canonical_aliases: dict[int, list[SkillAlias]] = {}
    for alias in aliases:
        if alias.is_canonical:
            canonical_aliases.setdefault(alias.concept_id, []).append(alias)

    for concept in concepts.values():
        canonical = canonical_aliases.get(concept.pk, [])
        if not canonical:
            issues.append(f"Skill concept '{concept.canonical_name}' has no canonical alias.")
        elif len(canonical) > 1:
            issues.append(
                f"Skill concept '{concept.canonical_name}' has multiple canonical aliases."
            )
        elif canonical[0].normalized_value != concept.canonical_key:
            issues.append(
                f"Canonical alias '{canonical[0].display_name}' does not match the key of "
                f"skill concept '{concept.canonical_name}'."
            )
        elif canonical[0].display_name != concept.canonical_name:
            issues.append(
                f"Canonical alias '{canonical[0].display_name}' does not match the name of "
                f"skill concept '{concept.canonical_name}'."
            )

    keys_by_concept = {concept.canonical_key: concept.pk for concept in concepts.values()}
    for alias in aliases:
        owner_pk = keys_by_concept.get(alias.normalized_value)
        if owner_pk is not None and owner_pk != alias.concept_id:
            issues.append(
                f"Skill alias '{alias.display_name}' shares its normalized value with the "
                "canonical key of another skill concept."
            )

    return tuple(issues)


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

    with catalog_operation():
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
