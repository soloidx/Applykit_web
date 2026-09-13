from typing import Any

from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join

from apps.skills.models import (
    SkillAlias,
    SkillCatalogAudit,
    SkillConcept,
    normalize_skill_label,
)
from apps.skills.services import (
    SkillConceptDetail,
    create_skill_alias,
    delete_skill_alias,
    preview_skill_alias_reassignment,
    reassign_skill_alias,
    rename_skill_alias,
    rename_skill_concept,
    skill_concept_detail,
)


def _alias_repair_links(alias_id: int) -> str:
    return format_html(
        '<a href="{}">reassign</a> | <a href="{}">delete</a>',
        reverse("admin:skills_skillalias_reassign", args=[alias_id]),
        reverse("admin:skills_skillalias_delete_audited", args=[alias_id]),
    )


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
    readonly_fields = ("canonical_key", "created_at", "updated_at", "catalog_references")

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

    @admin.display(description="Catalog references")
    def catalog_references(self, obj: SkillConcept | None = None) -> str:
        """Show aliases and content-free public and private reference counts."""

        if obj is None or obj.pk is None:
            return "-"
        detail = skill_concept_detail(concept=obj)
        return self._render_catalog_references(detail)

    def _render_catalog_references(self, detail: SkillConceptDetail) -> str:
        alias_rows = format_html_join(
            "",
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>",
            (
                (
                    alias.display_name,
                    alias.normalized_value,
                    "Canonical" if alias.is_canonical else "Alias",
                    alias.application_requirement_count,
                    _alias_repair_links(alias.alias_id)
                    if not alias.is_canonical
                    else format_html(
                        "<span>{}</span>",
                        "Rename the concept to change canonical wording.",
                    ),
                )
                for alias in detail.aliases
            ),
        )
        return format_html(
            "<table><thead><tr><th>Alias</th><th>Normalized</th><th>Kind</th>"
            "<th>Application skill requirements</th><th>Repair</th></tr></thead>"
            "<tbody>{}</tbody></table>"
            "<p>Public aliases: {}<br>"
            "Private references: {}<br>"
            "Profile candidate skill associations: {}<br>"
            "Experience candidate skill associations: {}<br>"
            "Project candidate skill associations: {}<br>"
            "Application skill requirements: {}</p>",
            alias_rows,
            detail.public_alias_count,
            detail.private_reference_count,
            detail.profile_skill_count,
            detail.experience_skill_count,
            detail.project_skill_count,
            detail.application_requirement_count,
        )


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

    def clean(self) -> None:
        super().clean()
        display_name = self.cleaned_data.get("display_name")
        concept = self.cleaned_data.get("concept")
        if not display_name or concept is None:
            return
        if self.instance.pk and concept.pk != self.instance.concept_id:
            raise ValidationError(
                "Alias reassignment needs its own audited skills-domain operation."
            )
        owner = SkillConcept.objects.filter(
            canonical_key=normalize_skill_label(display_name)
        ).first()
        if owner and owner.pk != concept.pk:
            raise ValidationError("This label already belongs to another skill concept.")


class _RepairReasonForm(forms.Form):
    template_name = ""

    reason = forms.CharField(
        widget=forms.Textarea,
        label="Repair reason",
        help_text="Required. Recorded on the immutable catalog audit.",
    )


class AliasReassignmentForm(_RepairReasonForm):
    template_name = "admin/skills/skillalias/reassign.html"

    destination = forms.ModelChoiceField(
        queryset=SkillConcept.objects.none(),
        label="Destination concept",
        help_text="Every requirement that references this alias will resolve to this concept.",
    )

    def __init__(self, *args: Any, alias: SkillAlias, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        destination = self.fields["destination"]
        if isinstance(destination, forms.ModelChoiceField):
            destination.queryset = SkillConcept.objects.exclude(pk=alias.concept_id).order_by(
                "canonical_name", "pk"
            )


class AliasDeletionForm(_RepairReasonForm):
    template_name = "admin/skills/skillalias/delete.html"


@admin.register(SkillAlias)
class SkillAliasAdmin(admin.ModelAdmin):
    form = SkillAliasAdminForm
    list_display = ("display_name", "normalized_value", "concept", "is_canonical", "repair")
    search_fields = ("display_name", "normalized_value", "concept__canonical_name")
    readonly_fields = ("normalized_value", "is_canonical", "created_at")

    def get_readonly_fields(self, request: Any, obj: SkillAlias | None = None) -> tuple[str, ...]:
        if obj and obj.is_canonical:
            return (*self.readonly_fields, "concept", "display_name")
        return self.readonly_fields

    def get_urls(self) -> list[Any]:
        custom = [
            path(
                "<path:object_id>/reassign/",
                self.admin_site.admin_view(self.reassign_view),
                name="skills_skillalias_reassign",
            ),
            path(
                "<path:object_id>/delete-audited/",
                self.admin_site.admin_view(self.delete_audited_view),
                name="skills_skillalias_delete_audited",
            ),
        ]
        return custom + super().get_urls()

    def save_model(
        self,
        request: Any,
        obj: SkillAlias,
        form: SkillAliasAdminForm,
        change: bool,
    ) -> None:
        if not change:
            create_skill_alias(
                concept=form.cleaned_data["concept"],
                display_name=form.cleaned_data["display_name"],
            )
        elif obj.is_canonical:
            super().save_model(request, obj, form, change)
        else:
            rename_skill_alias(alias=obj, display_name=form.cleaned_data["display_name"])

    def has_delete_permission(self, request: Any, obj: SkillAlias | None = None) -> bool:
        return False

    @admin.display(description="Repair")
    def repair(self, obj: SkillAlias) -> str:
        if obj.is_canonical:
            return "Rename the concept"
        return _alias_repair_links(obj.pk)

    def _context(
        self,
        request: HttpRequest,
        alias: SkillAlias,
        title: str,
        form: forms.Form,
    ) -> dict[str, Any]:
        return {
            **self.admin_site.each_context(request),
            "title": title,
            "alias": alias,
            "form": form,
            "opts": self.model._meta,
        }

    def reassign_view(self, request: HttpRequest, object_id: str) -> HttpResponse:
        if not self.has_change_permission(request):
            raise PermissionDenied
        alias = get_object_or_404(SkillAlias.objects.select_related("concept"), pk=object_id)
        title = f"Reassign alias {alias.display_name}"
        context: dict[str, Any] = self._context(
            request, alias, title, AliasReassignmentForm(alias=alias)
        )
        if request.method == "POST":
            form = AliasReassignmentForm(request.POST, alias=alias)
            context["form"] = form
            if form.is_valid():
                destination = form.cleaned_data["destination"]
                reason = form.cleaned_data["reason"]
                action = request.POST.get("action")
                if action == "preview":
                    try:
                        context["preview"] = preview_skill_alias_reassignment(
                            alias=alias,
                            destination_concept=destination,
                            actor=request.user,
                        )
                    except ValidationError as error:
                        form.add_error(None, error)
                    else:
                        return self._render(request, form, context)
                elif action == "confirm":
                    try:
                        reassign_skill_alias(
                            alias=alias,
                            destination_concept=destination,
                            actor=request.user,
                            reason=reason,
                            preview_token=request.POST.get("preview_token", ""),
                        )
                    except ValidationError as error:
                        form.add_error(None, error)
                    else:
                        self.message_user(
                            request,
                            f"Reassigned alias '{alias.display_name}' to {destination}.",
                        )
                        return redirect(reverse("admin:skills_skillalias_change", args=[alias.pk]))
        return self._render(request, form, context)

    def delete_audited_view(self, request: HttpRequest, object_id: str) -> HttpResponse:
        if not self.has_change_permission(request):
            raise PermissionDenied
        alias = get_object_or_404(SkillAlias.objects.select_related("concept"), pk=object_id)
        title = f"Delete alias {alias.display_name}"
        context: dict[str, Any] = self._context(request, alias, title, AliasDeletionForm())
        if request.method == "POST":
            form = AliasDeletionForm(request.POST)
            context["form"] = form
            if form.is_valid():
                try:
                    delete_skill_alias(
                        alias=alias,
                        actor=request.user,
                        reason=form.cleaned_data["reason"],
                    )
                except ValidationError as error:
                    form.add_error(None, error)
                else:
                    self.message_user(
                        request,
                        f"Deleted alias '{alias.display_name}' and recorded an audit.",
                    )
                    return redirect(reverse("admin:skills_skillalias_changelist"))
        return self._render(request, form, context)

    def _render(
        self,
        request: HttpRequest,
        form: _RepairReasonForm,
        context: dict[str, Any],
    ) -> TemplateResponse:
        return TemplateResponse(request, form.template_name, context)


@admin.register(SkillCatalogAudit)
class SkillCatalogAuditAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "operation",
        "actor_email",
        "alias_display_name",
        "source_concept_name",
        "destination_concept_name",
    )
    readonly_fields = tuple(field.name for field in SkillCatalogAudit._meta.fields)

    def has_add_permission(self, request: Any) -> bool:
        return False

    def has_change_permission(self, request: Any, obj: SkillCatalogAudit | None = None) -> bool:
        return False

    def has_delete_permission(self, request: Any, obj: SkillCatalogAudit | None = None) -> bool:
        return False
