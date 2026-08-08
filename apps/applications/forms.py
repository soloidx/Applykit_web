from typing import Any

from django import forms

from apps.applications.models import Company, JobApplication


class JobApplicationCreateForm(forms.ModelForm):
    company = forms.ModelChoiceField(queryset=Company.objects.order_by("name"), required=False)
    company_name = forms.CharField(max_length=255, required=False)
    website = forms.CharField(max_length=2048, required=False)
    posting_url = forms.URLField(required=False, assume_scheme="https")

    class Meta:
        model = JobApplication
        fields = [
            "company",
            "company_name",
            "website",
            "role_title",
            "job_description",
            "posting_url",
            "location",
            "compensation",
            "source",
            "private_notes",
        ]

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        if not cleaned_data.get("company") and not cleaned_data.get("company_name"):
            self.add_error(
                "company_name", "Select an existing company or enter a new company name."
            )
        return cleaned_data


class JobApplicationEditForm(forms.ModelForm):
    posting_url = forms.URLField(required=False, assume_scheme="https")

    class Meta:
        model = JobApplication
        fields = [
            "role_title",
            "job_description",
            "posting_url",
            "location",
            "compensation",
            "source",
            "private_notes",
        ]


class ApplicationStageForm(forms.Form):
    stage = forms.ChoiceField(choices=JobApplication.Stage.choices, label="Stage")
