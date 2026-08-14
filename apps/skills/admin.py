from typing import Any

from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError

from apps.skills.models import SkillAlias, SkillConcept, normalize_skill_label
from apps.skills.services import rename_skill_concept


class SkillConceptAdminForm(forms.ModelForm):
    class Meta:
        model = SkillConcept
        fields = ["canonical_name"]

    def clean_canonical_name(self) -> str:
        value = self.cleaned_data["canonical_name"].strip()
        if not value:
            raise ValidationError("Enter a skill concept name.")
        conflicts = SkillAlias.objects.filter(normalized_value=normalize_skill_label(value))
        if self.instance.pk:
            conflicts = conflicts.exclude(concept_id=self.instance.pk)
        conflict = conflicts.exists()
        if conflict:
            raise ValidationError("This label already belongs to another skill concept.")
        return value


@admin.register(SkillConcept)
class SkillConceptAdmin(admin.ModelAdmin):
    form = SkillConceptAdminForm
    list_display = ("canonical_name", "canonical_key", "created_at")
    search_fields = ("canonical_name", "canonical_key", "aliases__display_name")
    readonly_fields = ("canonical_key", "created_at", "updated_at")

    def save_model(
        self,
        request: Any,
        obj: SkillConcept,
        form: SkillConceptAdminForm,
        change: bool,
    ) -> None:
        if change:
            rename_skill_concept(concept=obj, canonical_name=form.cleaned_data["canonical_name"])
        else:
            super().save_model(request, obj, form, change)

    def has_delete_permission(self, request: Any, obj: SkillConcept | None = None) -> bool:
        return False


class SkillAliasAdminForm(forms.ModelForm):
    class Meta:
        model = SkillAlias
        fields = ["concept", "display_name"]

    def clean_display_name(self) -> str:
        value = self.cleaned_data["display_name"].strip()
        if not value:
            raise ValidationError("Enter a skill alias.")
        if (
            SkillAlias.objects.filter(normalized_value=normalize_skill_label(value))
            .exclude(pk=self.instance.pk)
            .exists()
        ):
            raise ValidationError("This label is already in the shared skill namespace.")
        return value


@admin.register(SkillAlias)
class SkillAliasAdmin(admin.ModelAdmin):
    form = SkillAliasAdminForm
    list_display = ("display_name", "normalized_value", "concept", "is_canonical")
    search_fields = ("display_name", "normalized_value", "concept__canonical_name")
    readonly_fields = ("normalized_value", "is_canonical", "created_at")

    def get_readonly_fields(self, request: Any, obj: SkillAlias | None = None) -> tuple[str, ...]:
        if obj and obj.is_canonical:
            return (*self.readonly_fields, "concept", "display_name")
        return self.readonly_fields

    def has_delete_permission(self, request: Any, obj: SkillAlias | None = None) -> bool:
        return False
