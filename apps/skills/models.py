import unicodedata
from typing import Any

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q


def normalize_skill_label(value: str) -> str:
    """Return the exact, Unicode-aware key used by the public skill namespace."""
    return unicodedata.normalize("NFKC", value.strip()).casefold()


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
        self.canonical_name = self.canonical_name.strip()
        self.canonical_key = normalize_skill_label(self.canonical_name)
        if not self.canonical_key:
            raise ValidationError("Enter a skill concept name.")

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
        self.display_name = self.display_name.strip()
        self.normalized_value = normalize_skill_label(self.display_name)
        if not self.normalized_value:
            raise ValidationError("Enter a skill alias.")
        super().save(*args, **kwargs)
