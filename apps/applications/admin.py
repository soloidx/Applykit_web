from typing import Any, cast

from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

from apps.applications.models import Company, CompanyDomainAlias
from apps.applications.services import merge_companies, normalized_registrable_domain


class CompanyAdminForm(forms.ModelForm):
    merge_into = forms.ModelChoiceField(
        queryset=Company.objects.none(),
        required=False,
        help_text=(
            "Move all applications and identities into this company, then delete this company."
        ),
    )

    class Meta:
        model = Company
        fields = ["name", "canonical_domain"]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            merge_field = cast(forms.ModelChoiceField, self.fields["merge_into"])
            merge_field.queryset = Company.objects.exclude(pk=self.instance.pk).order_by("name")

    def clean_canonical_domain(self) -> str | None:
        value = self.cleaned_data["canonical_domain"]
        if not value:
            return None
        domain = normalized_registrable_domain(value)
        conflict = (
            Company.objects.filter(domain_aliases__domain=domain)
            .exclude(pk=self.instance.pk)
            .exists()
        )
        if conflict:
            raise ValidationError("This domain already belongs to another company as an alias.")
        return domain

    def clean_name(self) -> str:
        name = self.cleaned_data["name"].strip()
        if not name:
            raise ValidationError("Enter a company name.")
        return name

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        merge_target = cleaned_data.get("merge_into")
        if merge_target and (
            cleaned_data.get("name") != self.instance.name
            or cleaned_data.get("canonical_domain") != self.instance.canonical_domain
        ):
            raise ValidationError("Correct a company before merging it into another company.")
        return cleaned_data


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    form = CompanyAdminForm
    list_display = ("name", "canonical_domain", "created_at")
    search_fields = ("name", "canonical_domain", "domain_aliases__domain")

    def save_model(self, request: Any, obj: Company, form: CompanyAdminForm, change: bool) -> None:
        merge_target = form.cleaned_data.get("merge_into")
        if merge_target:
            merge_companies(survivor=merge_target, duplicate=obj)
            return
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request: Any, obj: Company | None = None) -> bool:
        return False

    def response_change(self, request: HttpRequest, obj: Company) -> HttpResponse:
        merge_target_id = request.POST.get("merge_into")
        if merge_target_id and "_continue" in request.POST:
            return redirect(reverse("admin:applications_company_change", args=[merge_target_id]))
        return super().response_change(request, obj)


@admin.register(CompanyDomainAlias)
class CompanyDomainAliasAdmin(admin.ModelAdmin):
    class Form(forms.ModelForm):
        class Meta:
            model = CompanyDomainAlias
            fields = ["company", "domain"]

        def clean_domain(self) -> str:
            domain = normalized_registrable_domain(self.cleaned_data["domain"])
            if Company.objects.filter(canonical_domain=domain).exists():
                raise ValidationError("This domain is already a company's canonical domain.")
            return domain

    form = Form
    list_display = ("domain", "company")
    search_fields = ("domain", "company__name")

    def has_delete_permission(self, request: Any, obj: CompanyDomainAlias | None = None) -> bool:
        return False
