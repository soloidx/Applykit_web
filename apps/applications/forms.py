from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from django import forms
from django.utils import timezone

from apps.applications.models import Company, JobApplication, RecruitmentEvent


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


class RecruitmentEventForm(forms.ModelForm):
    scheduled_at = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"],
        widget=forms.DateTimeInput(
            attrs={
                "type": "datetime-local",
                "class": "mt-2 w-full rounded-xl border border-ink/20 bg-white px-4 py-3",
            },
            format="%Y-%m-%dT%H:%M",
        ),
    )

    class Meta:
        model = RecruitmentEvent
        fields = ["event_type", "custom_title", "scheduled_at", "status"]

    def __init__(self, *args: Any, timezone_name: str, **kwargs: Any) -> None:
        self.candidate_timezone = ZoneInfo(timezone_name)
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.initial["scheduled_at"] = timezone.localtime(
                self.instance.scheduled_at,
                self.candidate_timezone,
            ).replace(tzinfo=None)
        else:
            self.fields.pop("status")

    def clean_scheduled_at(self) -> datetime | None:
        value = self.cleaned_data.get("scheduled_at")
        if value is None:
            return None
        raw_value = self.data.get(self.add_prefix("scheduled_at"))
        if raw_value:
            try:
                submitted = datetime.fromisoformat(str(raw_value))
            except ValueError:
                return value
            if timezone.is_naive(submitted):
                return submitted.replace(tzinfo=self.candidate_timezone)
            return submitted.astimezone(self.candidate_timezone)
        if timezone.is_naive(value):
            return timezone.make_aware(value, self.candidate_timezone)
        return value.astimezone(self.candidate_timezone)

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        if (
            cleaned_data.get("event_type") == RecruitmentEvent.EventType.CUSTOM
            and not str(cleaned_data.get("custom_title", "")).strip()
        ):
            self.add_error("custom_title", "Enter a title for a custom event.")
        return cleaned_data
