from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from time import sleep
from typing import Any, TypeVar

from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError, connection, transaction
from django.db.models import Count, Q, QuerySet

from apps.skills.models import (
    SkillAlias,
    SkillCatalogAudit,
    SkillConcept,
    SkillConceptMergeAudit,
    catalog_operation,
    clean_skill_label,
    normalize_skill_label,
)

T = TypeVar("T")


class StaleCatalogPreviewError(ValidationError):
    """A confirmation no longer matches the catalog state that was previewed."""


@dataclass(frozen=True)
class ConceptAliasDetail:
    alias_id: int
    display_name: str
    normalized_value: str
    is_canonical: bool
    application_requirement_count: int


@dataclass(frozen=True)
class SkillConceptDetail:
    concept_id: int
    canonical_name: str
    canonical_key: str
    aliases: tuple[ConceptAliasDetail, ...]
    public_alias_count: int
    profile_skill_count: int
    experience_skill_count: int
    project_skill_count: int
    application_requirement_count: int

    @property
    def private_reference_count(self) -> int:
        return (
            self.profile_skill_count
            + self.experience_skill_count
            + self.project_skill_count
            + self.application_requirement_count
        )


@dataclass(frozen=True)
class ReassignmentCollision:
    application_id: int
    kept_requirement_id: int
    removed_requirement_id: int
    kept_classification: str
    promoted: bool


@dataclass(frozen=True)
class AliasReassignmentPreview:
    alias_id: int
    token: str
    affected_application_ids: tuple[int, ...]
    affected_requirement_ids: tuple[int, ...]
    moved_requirement_ids: tuple[int, ...]
    collisions: tuple[ReassignmentCollision, ...]


@dataclass(frozen=True)
class AliasReassignmentResult:
    alias: SkillAlias
    audit: SkillCatalogAudit


@dataclass(frozen=True)
class _ReassignmentPlan:
    token: str
    application_ids: tuple[int, ...]
    affected_requirement_ids: tuple[int, ...]
    moved_requirement_ids: tuple[int, ...]
    removed_requirement_ids: tuple[int, ...]
    collisions: tuple[ReassignmentCollision, ...]


@dataclass(frozen=True)
class MergedAliasDetail:
    alias_id: int
    display_name: str
    normalized_value: str
    is_canonical: bool
    requirement_count: int


@dataclass(frozen=True)
class SkillAssociationCollision:
    kind: str
    owner_id: int
    kept_id: int
    discarded_id: int
    kept_position: int


@dataclass(frozen=True)
class RequirementCollision:
    application_id: int
    kept_requirement_id: int
    discarded_requirement_ids: tuple[int, ...]
    kept_classification: str
    promoted: bool


@dataclass(frozen=True)
class ResumeSkillCollision:
    resume_id: int
    kept_resume_skill_id: int
    discarded_resume_skill_id: int
    included: bool
    position: int
    has_label_override: bool


@dataclass(frozen=True)
class SkillConceptMergePreview:
    loser_concept_id: int
    survivor_concept_id: int
    token: str
    aliases: tuple[MergedAliasDetail, ...]
    profile_skill_ids: tuple[int, ...]
    experience_skill_ids: tuple[int, ...]
    project_skill_ids: tuple[int, ...]
    requirement_ids: tuple[int, ...]
    resume_skill_ids: tuple[int, ...]
    skill_collisions: tuple[SkillAssociationCollision, ...]
    requirement_collisions: tuple[RequirementCollision, ...]
    resume_collisions: tuple[ResumeSkillCollision, ...]

    @property
    def discarded_profile_skill_ids(self) -> tuple[int, ...]:
        return tuple(
            collision.discarded_id
            for collision in self.skill_collisions
            if collision.kind == "profile"
        )

    @property
    def discarded_experience_skill_ids(self) -> tuple[int, ...]:
        return tuple(
            collision.discarded_id
            for collision in self.skill_collisions
            if collision.kind == "experience"
        )

    @property
    def discarded_project_skill_ids(self) -> tuple[int, ...]:
        return tuple(
            collision.discarded_id
            for collision in self.skill_collisions
            if collision.kind == "project"
        )

    @property
    def discarded_requirement_ids(self) -> tuple[int, ...]:
        return tuple(
            sorted(
                requirement_id
                for collision in self.requirement_collisions
                for requirement_id in collision.discarded_requirement_ids
            )
        )

    @property
    def discarded_resume_skill_ids(self) -> tuple[int, ...]:
        return tuple(collision.discarded_resume_skill_id for collision in self.resume_collisions)

    @property
    def promoted_requirement_ids(self) -> tuple[int, ...]:
        return tuple(
            collision.kept_requirement_id
            for collision in self.requirement_collisions
            if collision.promoted
        )


@dataclass(frozen=True)
class SkillConceptMergeResult:
    survivor: SkillConcept
    audit: SkillConceptMergeAudit


@dataclass(frozen=True)
class _AssociationMerge:
    kind: str
    model: Any
    owner_field: str
    loser_rows: tuple[Any, ...]
    survivor_rows: tuple[Any, ...]
    collisions: tuple[SkillAssociationCollision, ...]
    affected_ids: tuple[int, ...]
    discarded_ids: tuple[int, ...]


@dataclass(frozen=True)
class _RequirementMerge:
    rows: tuple[Any, ...]
    collisions: tuple[RequirementCollision, ...]
    affected_ids: tuple[int, ...]
    discarded_ids: tuple[int, ...]
    promoted_ids: tuple[int, ...]


@dataclass(frozen=True)
class _ResumeMerge:
    loser_rows: tuple[Any, ...]
    survivor_rows: tuple[Any, ...]
    collisions: tuple[ResumeSkillCollision, ...]
    affected_ids: tuple[int, ...]
    discarded_ids: tuple[int, ...]


@dataclass(frozen=True)
class _MergePlan:
    token: str
    aliases: tuple[SkillAlias, ...]
    alias_snapshots: tuple[dict[str, Any], ...]
    alias_requirement_counts: dict[int, int]
    profile: _AssociationMerge
    experience: _AssociationMerge
    project: _AssociationMerge
    requirements: _RequirementMerge
    resumes: _ResumeMerge


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


@transaction.atomic
def delete_skill_alias(*, alias: SkillAlias, actor: Any, reason: str) -> SkillCatalogAudit:
    """Delete one unreferenced noncanonical alias through the audited domain path.

    Canonical aliases cannot be deleted independently: the concept owns that
    wording. An alias referenced by any Application Skill Requirement is
    database-protected and cannot be removed. Only a noncanonical alias with no
    references may be hard-deleted, and every deletion records an immutable
    audit for the administrator and repair reason.
    """

    from apps.applications.models import ApplicationSkillRequirement

    _require_administrator(actor)
    repair_reason = _require_repair_reason(reason)

    locked = SkillAlias.objects.select_for_update().select_related("concept").get(pk=alias.pk)
    if locked.is_canonical:
        raise ValidationError(
            "Canonical skill aliases cannot be deleted; rename the concept instead."
        )
    if ApplicationSkillRequirement.objects.filter(alias=locked).exists():
        raise ValidationError("This skill alias is referenced and cannot be deleted.")

    source_concept = locked.concept
    alias_id = locked.pk
    alias_display_name = locked.display_name
    alias_normalized_value = locked.normalized_value

    with catalog_operation():
        locked.delete()

    return _write_catalog_audit(
        operation=SkillCatalogAudit.Operation.ALIAS_DELETION,
        actor=actor,
        reason=repair_reason,
        alias_id=alias_id,
        alias_display_name=alias_display_name,
        alias_normalized_value=alias_normalized_value,
        source_concept=source_concept,
    )


def skill_concept_detail(*, concept: SkillConcept) -> SkillConceptDetail:
    """Report the public wording and private reference counts of one concept.

    The detail is content-free: it counts private relationships without reading
    or exposing any candidate-owned content.
    """

    from apps.applications.models import ApplicationSkillRequirement
    from apps.profiles.models import ExperienceSkill, ProfileSkill, ProjectSkill

    aliases = list(SkillAlias.objects.filter(concept=concept).order_by("normalized_value", "pk"))
    requirement_counts = {
        row["alias_id"]: row["total"]
        for row in ApplicationSkillRequirement.objects.filter(alias__concept=concept)
        .values("alias_id")
        .annotate(total=Count("id"))
    }
    return SkillConceptDetail(
        concept_id=concept.pk,
        canonical_name=concept.canonical_name,
        canonical_key=concept.canonical_key,
        aliases=tuple(
            ConceptAliasDetail(
                alias_id=alias.pk,
                display_name=alias.display_name,
                normalized_value=alias.normalized_value,
                is_canonical=alias.is_canonical,
                application_requirement_count=requirement_counts.get(alias.pk, 0),
            )
            for alias in aliases
        ),
        public_alias_count=len(aliases),
        profile_skill_count=ProfileSkill.objects.filter(concept=concept).count(),
        experience_skill_count=ExperienceSkill.objects.filter(concept=concept).count(),
        project_skill_count=ProjectSkill.objects.filter(concept=concept).count(),
        application_requirement_count=ApplicationSkillRequirement.objects.filter(
            alias__concept=concept
        ).count(),
    )


def preview_skill_alias_reassignment(
    *,
    alias: SkillAlias,
    destination_concept: SkillConcept,
    actor: Any,
) -> AliasReassignmentPreview:
    """Preview the applications and collisions a reassignment would touch.

    The preview reads the current catalog without mutating it and returns an
    opaque token. Confirmation must present that token so a stale preview
    cannot overwrite a catalog that changed after the preview.
    """

    _require_administrator(actor)
    source_alias = SkillAlias.objects.select_related("concept").get(pk=alias.pk)
    destination = SkillConcept.objects.get(pk=destination_concept.pk)
    _validate_alias_reassignment(source_alias, destination)

    plan = _reassignment_plan(source_alias, destination, lock=False)
    return AliasReassignmentPreview(
        alias_id=source_alias.pk,
        token=plan.token,
        affected_application_ids=tuple(plan.application_ids),
        affected_requirement_ids=tuple(plan.affected_requirement_ids),
        moved_requirement_ids=tuple(plan.moved_requirement_ids),
        collisions=tuple(plan.collisions),
    )


@transaction.atomic
def reassign_skill_alias(
    *,
    alias: SkillAlias,
    destination_concept: SkillConcept,
    actor: Any,
    reason: str,
    preview_token: str,
) -> AliasReassignmentResult:
    """Atomically move one alias to another concept and repair collisions.

    Every affected Job Application is locked, every referencing requirement
    changes its effective concept, and any requirement that collides with a
    destination-mapped row keeps the destination row, promotes it to Required
    when either row was Required, and deletes the duplicate. Stale previews and
    any persistence error roll the entire operation back.
    """

    from apps.applications.models import ApplicationSkillRequirement, JobApplication

    _require_administrator(actor)
    repair_reason = _require_repair_reason(reason)
    if not preview_token:
        raise ValidationError("Preview the reassignment before confirming it.")

    locked_alias = SkillAlias.objects.select_for_update().select_related("concept").get(pk=alias.pk)
    destination = SkillConcept.objects.select_for_update().get(pk=destination_concept.pk)
    _validate_alias_reassignment(locked_alias, destination)

    application_ids = _affected_application_ids(locked_alias)
    list(JobApplication.objects.select_for_update().filter(pk__in=application_ids).order_by("pk"))
    plan = _reassignment_plan(locked_alias, destination, lock=True)

    if plan.token != preview_token:
        raise StaleCatalogPreviewError(
            "The catalog changed since this preview; preview the reassignment again."
        )

    promoted_ids = sorted(
        {collision.kept_requirement_id for collision in plan.collisions if collision.promoted}
    )
    source_concept = locked_alias.concept
    alias_id = locked_alias.pk
    alias_display_name = locked_alias.display_name
    alias_normalized_value = locked_alias.normalized_value

    if plan.removed_requirement_ids:
        ApplicationSkillRequirement.objects.filter(pk__in=plan.removed_requirement_ids).delete()
    if promoted_ids:
        ApplicationSkillRequirement.objects.filter(pk__in=promoted_ids).update(
            classification=ApplicationSkillRequirement.Classification.REQUIRED
        )
    with catalog_operation():
        locked_alias.concept = destination
        locked_alias.save(update_fields=["concept"])

    audit = _write_catalog_audit(
        operation=SkillCatalogAudit.Operation.ALIAS_REASSIGNMENT,
        actor=actor,
        reason=repair_reason,
        alias_id=alias_id,
        alias_display_name=alias_display_name,
        alias_normalized_value=alias_normalized_value,
        source_concept=source_concept,
        destination_concept=destination,
        affected_application_ids=plan.application_ids,
        affected_requirement_ids=plan.affected_requirement_ids,
        removed_requirement_ids=plan.removed_requirement_ids,
        promoted_requirement_ids=promoted_ids,
        kept_requirement_ids=[collision.kept_requirement_id for collision in plan.collisions],
        collision_application_ids=[collision.application_id for collision in plan.collisions],
    )
    return AliasReassignmentResult(alias=locked_alias, audit=audit)


def _require_administrator(actor: Any) -> None:
    if (
        actor is None
        or not getattr(actor, "is_active", False)
        or not getattr(actor, "is_staff", False)
    ):
        raise ValidationError("Only an administrator can repair the shared skill catalog.")


def _require_repair_reason(reason: str) -> str:
    cleaned = reason.strip()
    if not cleaned:
        raise ValidationError("Enter a repair reason.")
    return cleaned


def _validate_alias_reassignment(alias: SkillAlias, destination: SkillConcept) -> None:
    if alias.is_canonical:
        raise ValidationError("A canonical alias cannot be reassigned; rename the concept instead.")
    if alias.concept_id == destination.pk:
        raise ValidationError("Choose a different concept for this skill alias.")


def _affected_application_ids(alias: SkillAlias) -> list[int]:
    from apps.applications.models import ApplicationSkillRequirement

    return sorted(
        ApplicationSkillRequirement.objects.filter(alias=alias)
        .values_list("application_id", flat=True)
        .distinct()
    )


def _relevant_requirement_rows(
    alias: SkillAlias,
    destination: SkillConcept,
    application_ids: Iterable[int],
    *,
    lock: bool,
) -> list[Any]:
    """Return only the rows that can move or collide, never unrelated rows."""

    from apps.applications.models import ApplicationSkillRequirement

    rows = (
        ApplicationSkillRequirement.objects.select_related("alias")
        .filter(application_id__in=list(application_ids))
        .filter(Q(alias=alias) | Q(alias__concept=destination))
    )
    if lock:
        rows = rows.select_for_update()
    return list(rows.order_by("pk"))


def _reassignment_plan(
    alias: SkillAlias,
    destination: SkillConcept,
    *,
    lock: bool,
) -> _ReassignmentPlan:
    application_ids = _affected_application_ids(alias)
    rows = _relevant_requirement_rows(alias, destination, application_ids, lock=lock)
    collisions, removed_ids = _collision_plan(alias, destination, rows)
    source_rows = [row for row in rows if row.alias_id == alias.pk]
    kept_ids = {collision.kept_requirement_id for collision in collisions}
    return _ReassignmentPlan(
        token=_alias_reassignment_token(alias, destination, application_ids, rows),
        application_ids=tuple(application_ids),
        affected_requirement_ids=tuple(sorted({row.pk for row in source_rows} | kept_ids)),
        moved_requirement_ids=tuple(
            sorted(row.pk for row in source_rows if row.pk not in removed_ids)
        ),
        removed_requirement_ids=tuple(sorted(removed_ids)),
        collisions=tuple(collisions),
    )


def _collision_plan(
    alias: SkillAlias,
    destination: SkillConcept,
    rows: list[Any],
) -> tuple[list[ReassignmentCollision], set[int]]:
    """Resolve the one-effective-concept-per-application outcome.

    Existing destination-mapped rows are kept and collision rows are discarded;
    otherwise the first alias-backed row becomes the destination row. Required
    wins whenever any grouped row was Required.
    """

    from apps.applications.models import ApplicationSkillRequirement

    required = ApplicationSkillRequirement.Classification.REQUIRED
    destination_rows: dict[int, Any] = {}
    effective_classification: dict[int, str] = {}
    for row in rows:
        if row.alias_id != alias.pk and row.alias.concept_id == destination.pk:
            destination_rows.setdefault(row.application_id, row)
            effective_classification[row.application_id] = row.classification

    collisions: list[ReassignmentCollision] = []
    removed_ids: set[int] = set()
    for row in rows:
        if row.alias_id != alias.pk:
            continue
        application_id = row.application_id
        kept = destination_rows.get(application_id)
        if kept is None:
            destination_rows[application_id] = row
            effective_classification[application_id] = row.classification
            continue
        promoted = (
            row.classification == required and effective_classification[application_id] != required
        )
        if promoted:
            effective_classification[application_id] = required
        removed_ids.add(row.pk)
        collisions.append(
            ReassignmentCollision(
                application_id=application_id,
                kept_requirement_id=kept.pk,
                removed_requirement_id=row.pk,
                kept_classification=effective_classification[application_id],
                promoted=promoted,
            )
        )
    return collisions, removed_ids


def _alias_reassignment_token(
    alias: SkillAlias,
    destination: SkillConcept,
    application_ids: Iterable[int],
    rows: list[Any],
) -> str:
    payload = {
        "alias": [alias.pk, alias.concept_id, alias.normalized_value],
        "destination": [destination.pk, destination.canonical_key],
        "applications": sorted(application_ids),
        "rows": sorted(
            [
                row.pk,
                row.application_id,
                row.alias_id,
                row.alias.concept_id,
                row.classification,
            ]
            for row in rows
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_catalog_audit(
    *,
    operation: str,
    actor: Any,
    reason: str,
    alias_id: int,
    alias_display_name: str,
    alias_normalized_value: str,
    source_concept: SkillConcept,
    destination_concept: SkillConcept | None = None,
    affected_application_ids: Iterable[int] = (),
    affected_requirement_ids: Iterable[int] = (),
    removed_requirement_ids: Iterable[int] = (),
    promoted_requirement_ids: Iterable[int] = (),
    kept_requirement_ids: Iterable[int] = (),
    collision_application_ids: Iterable[int] = (),
) -> SkillCatalogAudit:
    affected_requirements = sorted(affected_requirement_ids)
    return SkillCatalogAudit.objects.create(
        operation=operation,
        actor_id=actor.pk,
        actor_email=actor.email,
        reason=reason,
        alias_id=alias_id,
        alias_display_name=alias_display_name,
        alias_normalized_value=alias_normalized_value,
        source_concept_id=source_concept.pk,
        source_concept_name=source_concept.canonical_name,
        source_concept_key=source_concept.canonical_key,
        destination_concept_id=destination_concept.pk if destination_concept else None,
        destination_concept_name=(
            destination_concept.canonical_name if destination_concept else ""
        ),
        destination_concept_key=destination_concept.canonical_key if destination_concept else "",
        affected_application_ids=sorted(affected_application_ids),
        affected_requirement_ids=affected_requirements,
        removed_requirement_ids=sorted(removed_requirement_ids),
        promoted_requirement_ids=sorted(promoted_requirement_ids),
        kept_requirement_ids=sorted(kept_requirement_ids),
        collision_application_ids=sorted(collision_application_ids),
        affected_requirement_count=len(affected_requirements),
    )


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

    issues.extend(_dangling_reference_issues())
    issues.extend(_duplicate_effective_concept_issues())
    return tuple(issues)


def _dangling_reference_issues() -> list[str]:
    """Private and alias rows must resolve to an existing catalog record."""

    from apps.applications.models import ApplicationSkillRequirement
    from apps.profiles.models import ExperienceSkill, ProfileSkill, ProjectSkill

    concept_ids = SkillConcept.objects.values("id")
    alias_ids = SkillAlias.objects.values("id")
    locations = (
        ("skill alias", SkillAlias.objects.exclude(concept_id__in=concept_ids)),
        ("profile skill", ProfileSkill.objects.exclude(concept_id__in=concept_ids)),
        ("experience skill", ExperienceSkill.objects.exclude(concept_id__in=concept_ids)),
        ("project skill", ProjectSkill.objects.exclude(concept_id__in=concept_ids)),
        (
            "application skill requirement",
            ApplicationSkillRequirement.objects.exclude(alias_id__in=alias_ids),
        ),
    )

    issues: list[str] = []
    for label, queryset in locations:
        dangling = queryset.count()
        if dangling:
            issues.append(f"{dangling} {label}(s) reference a missing catalog record.")
    return issues


def _duplicate_effective_concept_issues() -> list[str]:
    """Each private location may reference an effective concept only once."""

    from apps.applications.models import ApplicationSkillRequirement
    from apps.profiles.models import ExperienceSkill, ProfileSkill, ProjectSkill

    issues: list[str] = []
    issues.extend(
        _repeated_concept_issues(
            ApplicationSkillRequirement.objects.values("application_id", "alias__concept_id"),
            label="Application",
            owner_field="application_id",
            concept_field="alias__concept_id",
        )
    )
    issues.extend(
        _repeated_concept_issues(
            ProfileSkill.objects.values("profile_id", "concept_id"),
            label="Profile",
            owner_field="profile_id",
            concept_field="concept_id",
        )
    )
    issues.extend(
        _repeated_concept_issues(
            ExperienceSkill.objects.values("experience_id", "concept_id"),
            label="Experience",
            owner_field="experience_id",
            concept_field="concept_id",
        )
    )
    issues.extend(
        _repeated_concept_issues(
            ProjectSkill.objects.values("project_id", "concept_id"),
            label="Project",
            owner_field="project_id",
            concept_field="concept_id",
        )
    )
    return issues


def _repeated_concept_issues(
    rows: QuerySet[Any, Any],
    *,
    label: str,
    owner_field: str,
    concept_field: str,
) -> list[str]:
    issues: list[str] = []
    duplicates = rows.annotate(total=Count("id")).filter(total__gt=1)
    for row in duplicates:
        issues.append(
            f"{label} {row[owner_field]} repeats skill concept {row[concept_field]} "
            f"{row['total']} times."
        )
    return issues


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


def preview_skill_concept_merge(
    *,
    loser: SkillConcept,
    survivor: SkillConcept,
    actor: Any,
) -> SkillConceptMergePreview:
    """Preview the aliases, references, and collisions a concept merge would resolve.

    The preview reads the current catalog without mutating it and returns an
    opaque token. Confirmation must present that token so a stale preview
    cannot overwrite a catalog that changed after the preview.
    """

    _require_administrator(actor)
    source = SkillConcept.objects.get(pk=loser.pk)
    destination = SkillConcept.objects.get(pk=survivor.pk)
    _validate_concept_merge(source, destination)
    plan = _merge_plan(source, destination, lock=False)
    return _merge_preview_from_plan(source, destination, plan)


@transaction.atomic
def merge_skill_concept(
    *,
    loser: SkillConcept,
    survivor: SkillConcept,
    actor: Any,
    reason: str,
    preview_token: str,
) -> SkillConceptMergeResult:
    """Atomically merge one Skill Concept into a surviving concept.

    Every alias and reference moves to the survivor, every private-location
    collision resolves to a single row, the losing Concept is hard-deleted, and
    an immutable, content-free audit is appended. Stale previews and any
    persistence error roll the entire operation back.
    """

    _require_administrator(actor)
    merge_reason = _require_repair_reason(reason)
    if not preview_token:
        raise ValidationError("Preview the merge before confirming it.")

    from apps.applications.models import JobApplication

    locked_loser = SkillConcept.objects.select_for_update().get(pk=loser.pk)
    locked_survivor = SkillConcept.objects.select_for_update().get(pk=survivor.pk)
    _validate_concept_merge(locked_loser, locked_survivor)
    list(
        JobApplication.objects.select_for_update()
        .filter(pk__in=_merge_affected_application_ids(locked_loser))
        .order_by("pk")
    )
    plan = _merge_plan(locked_loser, locked_survivor, lock=True)

    if plan.token != preview_token:
        raise StaleCatalogPreviewError(
            "The catalog changed since this preview; preview the merge again."
        )

    loser_snapshot = (locked_loser.pk, locked_loser.canonical_name, locked_loser.canonical_key)
    survivor_snapshot = (
        locked_survivor.pk,
        locked_survivor.canonical_name,
        locked_survivor.canonical_key,
    )
    _execute_merge(plan, locked_loser, locked_survivor)
    audit = _write_merge_audit(
        actor=actor,
        reason=merge_reason,
        loser_snapshot=loser_snapshot,
        survivor_snapshot=survivor_snapshot,
        plan=plan,
    )
    return SkillConceptMergeResult(survivor=locked_survivor, audit=audit)


def _validate_concept_merge(loser: SkillConcept, survivor: SkillConcept) -> None:
    if loser.pk == survivor.pk:
        raise ValidationError("Choose a different concept to merge into.")


def _merge_affected_application_ids(loser: SkillConcept) -> list[int]:
    from apps.applications.models import ApplicationSkillRequirement

    return sorted(
        ApplicationSkillRequirement.objects.filter(alias__concept=loser)
        .values_list("application_id", flat=True)
        .distinct()
    )


def _lock_filter(queryset: QuerySet[Any, Any], lock: bool) -> QuerySet[Any, Any]:
    return queryset.select_for_update() if lock else queryset


def _merge_plan(
    loser: SkillConcept,
    survivor: SkillConcept,
    *,
    lock: bool,
) -> _MergePlan:
    from apps.applications.models import ApplicationSkillRequirement
    from apps.profiles.models import ExperienceSkill, ProfileSkill, ProjectSkill

    aliases = tuple(
        _lock_filter(SkillAlias.objects.filter(concept=loser), lock).order_by(
            "normalized_value", "pk"
        )
    )
    alias_snapshots = tuple(
        {
            "id": alias.pk,
            "display_name": alias.display_name,
            "normalized_value": alias.normalized_value,
            "was_canonical": alias.is_canonical,
        }
        for alias in aliases
    )
    alias_requirement_counts = {
        row["alias_id"]: row["total"]
        for row in ApplicationSkillRequirement.objects.filter(
            alias_id__in=[alias.pk for alias in aliases]
        )
        .values("alias_id")
        .annotate(total=Count("id"))
    }
    profile = _association_merge(
        kind="profile",
        model=ProfileSkill,
        owner_field="profile",
        loser=loser,
        survivor=survivor,
        lock=lock,
    )
    experience = _association_merge(
        kind="experience",
        model=ExperienceSkill,
        owner_field="experience",
        loser=loser,
        survivor=survivor,
        lock=lock,
    )
    project = _association_merge(
        kind="project",
        model=ProjectSkill,
        owner_field="project",
        loser=loser,
        survivor=survivor,
        lock=lock,
    )
    requirements = _requirement_merge(loser=loser, survivor=survivor, lock=lock)
    resumes = _resume_merge(loser=loser, survivor=survivor, lock=lock)
    token = _merge_token(
        loser,
        survivor,
        aliases,
        profile,
        experience,
        project,
        requirements,
        resumes,
    )
    return _MergePlan(
        token=token,
        aliases=aliases,
        alias_snapshots=alias_snapshots,
        alias_requirement_counts=alias_requirement_counts,
        profile=profile,
        experience=experience,
        project=project,
        requirements=requirements,
        resumes=resumes,
    )


def _association_merge(
    *,
    kind: str,
    model: Any,
    owner_field: str,
    loser: SkillConcept,
    survivor: SkillConcept,
    lock: bool,
) -> _AssociationMerge:
    owner_column = f"{owner_field}_id"
    loser_rows = list(_lock_filter(model.objects.filter(concept=loser), lock).order_by("pk"))
    owner_ids = sorted({getattr(row, owner_column) for row in loser_rows})
    survivor_rows = list(
        _lock_filter(
            model.objects.filter(concept=survivor, **{f"{owner_column}__in": owner_ids}),
            lock,
        ).order_by("pk")
    )
    survivor_by_owner = {getattr(row, owner_column): row for row in survivor_rows}

    collisions: list[SkillAssociationCollision] = []
    discarded: list[int] = []
    affected: set[int] = set()
    for row in loser_rows:
        affected.add(row.pk)
        survivor_row = survivor_by_owner.get(getattr(row, owner_column))
        if survivor_row is None:
            continue
        affected.add(survivor_row.pk)
        discarded.append(row.pk)
        collisions.append(
            SkillAssociationCollision(
                kind=kind,
                owner_id=getattr(row, owner_column),
                kept_id=survivor_row.pk,
                discarded_id=row.pk,
                kept_position=min(survivor_row.position, row.position),
            )
        )
    return _AssociationMerge(
        kind=kind,
        model=model,
        owner_field=owner_field,
        loser_rows=tuple(loser_rows),
        survivor_rows=tuple(survivor_rows),
        collisions=tuple(collisions),
        affected_ids=tuple(sorted(affected)),
        discarded_ids=tuple(sorted(discarded)),
    )


def _requirement_merge(
    *,
    loser: SkillConcept,
    survivor: SkillConcept,
    lock: bool,
) -> _RequirementMerge:
    from apps.applications.models import ApplicationSkillRequirement

    loser_alias_ids = set(SkillAlias.objects.filter(concept=loser).values_list("pk", flat=True))
    survivor_alias_ids = set(
        SkillAlias.objects.filter(concept=survivor).values_list("pk", flat=True)
    )
    relevant_alias_ids = loser_alias_ids | survivor_alias_ids
    affected_applications = ApplicationSkillRequirement.objects.filter(
        alias_id__in=loser_alias_ids
    ).values("application_id")
    queryset = ApplicationSkillRequirement.objects.select_related("alias").filter(
        alias_id__in=relevant_alias_ids,
        application_id__in=affected_applications,
    )
    rows = list(_lock_filter(queryset, lock).order_by("pk"))

    grouped: dict[int, list[Any]] = {}
    for row in rows:
        grouped.setdefault(row.application_id, []).append(row)

    required = ApplicationSkillRequirement.Classification.REQUIRED
    collisions: list[RequirementCollision] = []
    discarded: list[int] = []
    promoted: list[int] = []
    affected: set[int] = set()
    for application_id, group in grouped.items():
        affected.update(row.pk for row in group)
        survivor_group = [row for row in group if row.alias_id in survivor_alias_ids]
        keeper = survivor_group[0] if survivor_group else group[0]
        removed = sorted(row.pk for row in group if row.pk != keeper.pk)
        if not removed:
            continue
        is_promoted = keeper.classification != required and any(
            row.classification == required for row in group
        )
        if is_promoted:
            promoted.append(keeper.pk)
        discarded.extend(removed)
        collisions.append(
            RequirementCollision(
                application_id=application_id,
                kept_requirement_id=keeper.pk,
                discarded_requirement_ids=tuple(removed),
                kept_classification=required if is_promoted else keeper.classification,
                promoted=is_promoted,
            )
        )
    return _RequirementMerge(
        rows=tuple(rows),
        collisions=tuple(collisions),
        affected_ids=tuple(sorted(affected)),
        discarded_ids=tuple(sorted(discarded)),
        promoted_ids=tuple(sorted(promoted)),
    )


def _resume_merge(
    *,
    loser: SkillConcept,
    survivor: SkillConcept,
    lock: bool,
) -> _ResumeMerge:
    from apps.resumes.models import ResumeSkill

    loser_rows = list(_lock_filter(ResumeSkill.objects.filter(concept=loser), lock).order_by("pk"))
    resume_ids = sorted({row.resume_id for row in loser_rows})
    survivor_rows = list(
        _lock_filter(
            ResumeSkill.objects.filter(concept=survivor, resume_id__in=resume_ids),
            lock,
        ).order_by("pk")
    )
    survivor_by_resume = {row.resume_id: row for row in survivor_rows}

    collisions: list[ResumeSkillCollision] = []
    discarded: list[int] = []
    affected: set[int] = set()
    for row in loser_rows:
        affected.add(row.pk)
        survivor_row = survivor_by_resume.get(row.resume_id)
        if survivor_row is None:
            continue
        discarded.append(row.pk)
        collisions.append(
            ResumeSkillCollision(
                resume_id=row.resume_id,
                kept_resume_skill_id=survivor_row.pk,
                discarded_resume_skill_id=row.pk,
                included=survivor_row.included,
                position=survivor_row.position,
                has_label_override=survivor_row.label_override is not None,
            )
        )
    return _ResumeMerge(
        loser_rows=tuple(loser_rows),
        survivor_rows=tuple(survivor_rows),
        collisions=tuple(collisions),
        affected_ids=tuple(sorted(affected)),
        discarded_ids=tuple(sorted(discarded)),
    )


def _merge_token(
    loser: SkillConcept,
    survivor: SkillConcept,
    aliases: tuple[SkillAlias, ...],
    profile: _AssociationMerge,
    experience: _AssociationMerge,
    project: _AssociationMerge,
    requirements: _RequirementMerge,
    resumes: _ResumeMerge,
) -> str:
    association_snapshot: list[list[Any]] = []
    for merge in (profile, experience, project):
        owner_column = f"{merge.owner_field}_id"
        for row in (*merge.loser_rows, *merge.survivor_rows):
            association_snapshot.append(
                [
                    merge.kind,
                    row.pk,
                    getattr(row, owner_column),
                    row.concept_id,
                    row.position,
                ]
            )
    payload = {
        "loser": [loser.pk, loser.canonical_key, loser.canonical_name],
        "survivor": [survivor.pk, survivor.canonical_key, survivor.canonical_name],
        "aliases": sorted(
            [
                alias.pk,
                alias.concept_id,
                alias.normalized_value,
                alias.is_canonical,
                alias.display_name,
            ]
            for alias in aliases
        ),
        "associations": sorted(association_snapshot, key=lambda item: (item[0], item[1])),
        "requirements": sorted(
            [
                row.pk,
                row.application_id,
                row.alias_id,
                row.alias.concept_id,
                row.classification,
            ]
            for row in requirements.rows
        ),
        "resumes": sorted(
            [
                row.pk,
                row.resume_id,
                row.concept_id,
                row.included,
                row.position,
                row.label_override or "",
            ]
            for row in (*resumes.loser_rows, *resumes.survivor_rows)
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _merge_preview_from_plan(
    loser: SkillConcept,
    survivor: SkillConcept,
    plan: _MergePlan,
) -> SkillConceptMergePreview:
    aliases = tuple(
        MergedAliasDetail(
            alias_id=alias.pk,
            display_name=alias.display_name,
            normalized_value=alias.normalized_value,
            is_canonical=alias.is_canonical,
            requirement_count=plan.alias_requirement_counts.get(alias.pk, 0),
        )
        for alias in plan.aliases
    )
    return SkillConceptMergePreview(
        loser_concept_id=loser.pk,
        survivor_concept_id=survivor.pk,
        token=plan.token,
        aliases=aliases,
        profile_skill_ids=plan.profile.affected_ids,
        experience_skill_ids=plan.experience.affected_ids,
        project_skill_ids=plan.project.affected_ids,
        requirement_ids=plan.requirements.affected_ids,
        resume_skill_ids=plan.resumes.affected_ids,
        skill_collisions=(
            *plan.profile.collisions,
            *plan.experience.collisions,
            *plan.project.collisions,
        ),
        requirement_collisions=plan.requirements.collisions,
        resume_collisions=plan.resumes.collisions,
    )


def _execute_merge(
    plan: _MergePlan,
    loser: SkillConcept,
    survivor: SkillConcept,
) -> None:
    from apps.applications.models import ApplicationSkillRequirement
    from apps.resumes.models import ResumeSkill

    for merge in (plan.profile, plan.experience, plan.project):
        _execute_association_merge(merge, survivor)

    if plan.requirements.discarded_ids:
        ApplicationSkillRequirement.objects.filter(pk__in=plan.requirements.discarded_ids).delete()
    if plan.requirements.promoted_ids:
        ApplicationSkillRequirement.objects.filter(pk__in=plan.requirements.promoted_ids).update(
            classification=ApplicationSkillRequirement.Classification.REQUIRED
        )

    if plan.resumes.discarded_ids:
        ResumeSkill.objects.filter(pk__in=plan.resumes.discarded_ids).delete()
    moved_resume_ids = [
        row.pk for row in plan.resumes.loser_rows if row.pk not in set(plan.resumes.discarded_ids)
    ]
    if moved_resume_ids:
        ResumeSkill.objects.filter(pk__in=moved_resume_ids).update(concept=survivor)

    with catalog_operation():
        for alias in plan.aliases:
            alias.concept = survivor
            alias.is_canonical = False
            alias.save(update_fields=["concept", "is_canonical"])
        loser.delete()


def _execute_association_merge(merge: _AssociationMerge, survivor: SkillConcept) -> None:
    model = merge.model
    owner_column = f"{merge.owner_field}_id"
    for collision in merge.collisions:
        model.objects.filter(pk=collision.kept_id).update(position=collision.kept_position)
    discarded = set(merge.discarded_ids)
    moved_ids = [row.pk for row in merge.loser_rows if row.pk not in discarded]
    if moved_ids:
        model.objects.filter(pk__in=moved_ids).update(concept=survivor)
    if merge.discarded_ids:
        model.objects.filter(pk__in=merge.discarded_ids).delete()
    for owner_id in sorted({collision.owner_id for collision in merge.collisions}):
        rows = model.objects.filter(**{owner_column: owner_id}).order_by("position", "id")
        for position, row in enumerate(rows):
            if row.position != position:
                model.objects.filter(pk=row.pk).update(position=position)


def _write_merge_audit(
    *,
    actor: Any,
    reason: str,
    loser_snapshot: tuple[int, str, str],
    survivor_snapshot: tuple[int, str, str],
    plan: _MergePlan,
) -> SkillConceptMergeAudit:
    collision_outcomes = (
        [
            {
                "kind": collision.kind,
                "owner_id": collision.owner_id,
                "kept_id": collision.kept_id,
                "discarded_id": collision.discarded_id,
                "kept_position": collision.kept_position,
            }
            for collision in (
                *plan.profile.collisions,
                *plan.experience.collisions,
                *plan.project.collisions,
            )
        ]
        + [
            {
                "kind": "requirement",
                "application_id": collision.application_id,
                "kept_id": collision.kept_requirement_id,
                "discarded_ids": list(collision.discarded_requirement_ids),
                "kept_classification": collision.kept_classification,
                "promoted": collision.promoted,
            }
            for collision in plan.requirements.collisions
        ]
        + [
            {
                "kind": "resume_skill",
                "resume_id": collision.resume_id,
                "kept_id": collision.kept_resume_skill_id,
                "discarded_id": collision.discarded_resume_skill_id,
                "included": collision.included,
                "position": collision.position,
                "has_label_override": collision.has_label_override,
            }
            for collision in plan.resumes.collisions
        ]
    )
    affected_relationship_count = (
        len(plan.profile.affected_ids)
        + len(plan.experience.affected_ids)
        + len(plan.project.affected_ids)
        + len(plan.requirements.affected_ids)
        + len(plan.resumes.affected_ids)
    )
    loser_id, loser_name, loser_key = loser_snapshot
    survivor_id, survivor_name, survivor_key = survivor_snapshot
    affected_application_ids = sorted({row.application_id for row in plan.requirements.rows})
    return SkillConceptMergeAudit.objects.create(
        actor_id=actor.pk,
        actor_email=actor.email,
        reason=reason,
        loser_concept_id=loser_id,
        loser_concept_name=loser_name,
        loser_concept_key=loser_key,
        survivor_concept_id=survivor_id,
        survivor_concept_name=survivor_name,
        survivor_concept_key=survivor_key,
        moved_alias_ids=[snapshot["id"] for snapshot in plan.alias_snapshots],
        moved_alias_snapshots=list(plan.alias_snapshots),
        affected_application_ids=affected_application_ids,
        affected_profile_skill_ids=list(plan.profile.affected_ids),
        affected_experience_skill_ids=list(plan.experience.affected_ids),
        affected_project_skill_ids=list(plan.project.affected_ids),
        affected_requirement_ids=list(plan.requirements.affected_ids),
        affected_resume_skill_ids=list(plan.resumes.affected_ids),
        discarded_profile_skill_ids=list(plan.profile.discarded_ids),
        discarded_experience_skill_ids=list(plan.experience.discarded_ids),
        discarded_project_skill_ids=list(plan.project.discarded_ids),
        discarded_requirement_ids=list(plan.requirements.discarded_ids),
        discarded_resume_skill_ids=list(plan.resumes.discarded_ids),
        promoted_requirement_ids=list(plan.requirements.promoted_ids),
        collision_outcomes=collision_outcomes,
        affected_relationship_count=affected_relationship_count,
    )
