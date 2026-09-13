import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q

_catalog_operation = ContextVar("skill_catalog_operation", default=False)


@contextmanager
def catalog_operation() -> Iterator[None]:
    """Permit catalog wording mutations from transactional skills-domain operations."""
    token = _catalog_operation.set(True)
    try:
        yield
    finally:
        _catalog_operation.reset(token)


def normalize_skill_label(value: str) -> str:
    """Return the exact, Unicode-aware key used by the public skill namespace."""
    return unicodedata.normalize("NFKC", value.strip()).casefold()


def clean_skill_label(value: str) -> str:
    """Trim an entered skill label while preserving its display text."""
    label = value.strip()
    if not label:
        raise ValidationError("Enter a hard-skill label.")
    return label


class SkillConcept(models.Model):
    canonical_name = models.CharField(max_length=200)
    canonical_key = models.CharField(max_length=200, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["canonical_name", "pk"]
        verbose_name = "skill concept"
        verbose_name_plural = "skill concepts"

    def __str__(self) -> str:
        return self.canonical_name

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.canonical_name = clean_skill_label(self.canonical_name)
        if not self._state.adding and not _catalog_operation.get():
            persisted_name = (
                SkillConcept._base_manager.filter(pk=self.pk)
                .values_list("canonical_name", flat=True)
                .first()
            )
            if persisted_name is not None and self.canonical_name != persisted_name:
                raise ValidationError("Rename a skill concept through the skills-domain operation.")
        self.canonical_key = normalize_skill_label(self.canonical_name)

        adding = self._state.adding
        with transaction.atomic():
            super().save(*args, **kwargs)
            if adding:
                SkillAlias.objects.create(
                    concept=self,
                    display_name=self.canonical_name,
                    is_canonical=True,
                )
            else:
                canonical_alias = SkillAlias.objects.get(concept=self, is_canonical=True)
                canonical_alias.display_name = self.canonical_name
                canonical_alias.save(update_fields=["display_name", "normalized_value"])

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        if not _catalog_operation.get():
            raise ValidationError("Delete a skill concept through the skills-domain operation.")
        return super().delete(*args, **kwargs)


class SkillAlias(models.Model):
    concept = models.ForeignKey(
        SkillConcept,
        on_delete=models.CASCADE,
        related_name="aliases",
    )
    display_name = models.CharField(max_length=200)
    normalized_value = models.CharField(max_length=200, unique=True, editable=False)
    is_canonical = models.BooleanField(default=False, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["normalized_value", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["concept"],
                condition=Q(is_canonical=True),
                name="skill_alias_one_canonical_per_concept",
            )
        ]

    def __str__(self) -> str:
        return self.display_name

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.display_name = clean_skill_label(self.display_name)
        normalized_value = normalize_skill_label(self.display_name)
        if not self._state.adding and not _catalog_operation.get():
            persisted = (
                SkillAlias._base_manager.filter(pk=self.pk)
                .values_list("is_canonical", "normalized_value", "concept_id")
                .first()
            )
            if persisted is not None:
                persisted_canonical, persisted_value, persisted_concept_id = persisted
                if self.is_canonical != persisted_canonical:
                    raise ValidationError(
                        "Change a canonical skill alias through the skills-domain operation."
                    )
                if normalized_value != persisted_value:
                    if persisted_canonical:
                        raise ValidationError(
                            "Rename the skill concept instead of editing its canonical alias."
                        )
                    raise ValidationError(
                        "Correct a skill alias through the skills-domain operation."
                    )
                if self.concept_id != persisted_concept_id:
                    raise ValidationError(
                        "Reassign a skill alias through its dedicated skills-domain operation."
                    )
        self.normalized_value = normalized_value
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        if not _catalog_operation.get():
            raise ValidationError("Delete a skill alias through the skills-domain operation.")
        return super().delete(*args, **kwargs)


class SkillCatalogAudit(models.Model):
    """An append-only record of one administrative catalog repair.

    Audits are immutable scalar snapshots: they record the actor, the required
    reason, public wording and normalized values, the before/after concept
    identity, and the affected private relationship IDs and counts. They never
    store private content such as role titles, descriptions, account emails of
    candidates, or company names. They survive the repair entity and are never
    updated or deleted.
    """

    class Operation(models.TextChoices):
        ALIAS_REASSIGNMENT = "alias_reassignment", "Alias reassignment"
        ALIAS_DELETION = "alias_deletion", "Alias deletion"

    operation = models.CharField(max_length=32, choices=Operation.choices)
    actor_id = models.BigIntegerField()
    actor_email = models.CharField(max_length=254)
    reason = models.TextField()
    alias_id = models.BigIntegerField()
    alias_display_name = models.CharField(max_length=200)
    alias_normalized_value = models.CharField(max_length=200)
    source_concept_id = models.BigIntegerField()
    source_concept_name = models.CharField(max_length=200)
    source_concept_key = models.CharField(max_length=200)
    destination_concept_id = models.BigIntegerField(null=True, blank=True)
    destination_concept_name = models.CharField(max_length=200, blank=True)
    destination_concept_key = models.CharField(max_length=200, blank=True)
    affected_application_ids = models.JSONField(default=list)
    affected_requirement_ids = models.JSONField(default=list)
    removed_requirement_ids = models.JSONField(default=list)
    promoted_requirement_ids = models.JSONField(default=list)
    kept_requirement_ids = models.JSONField(default=list)
    collision_application_ids = models.JSONField(default=list)
    affected_requirement_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "skill catalog audit"
        verbose_name_plural = "skill catalog audits"

    def __str__(self) -> str:
        return f"{self.get_operation_display()} for alias {self.alias_display_name}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Skill catalog audits are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Skill catalog audits are immutable.")
