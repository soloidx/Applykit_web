from __future__ import annotations

from typing import Any

from django import forms

from apps.profiles.models import CandidateProfile, validate_iana_timezone


class CandidateProfileForm(forms.ModelForm):
    class Meta:
        model = CandidateProfile
        fields = [
            "full_name",
            "timezone",
            "professional_title",
            "professional_summary",
            "phone_number",
            "location",
            "linkedin_url",
            "portfolio_url",
        ]
        labels = {
            "full_name": "Full name",
            "timezone": "Timezone",
            "professional_title": "Professional title",
            "professional_summary": "Professional summary",
            "phone_number": "Phone number",
            "location": "Location",
            "linkedin_url": "LinkedIn URL",
            "portfolio_url": "Portfolio URL",
        }
        help_texts = {
            "timezone": "Use an IANA timezone such as Europe/London or America/New_York.",
            "professional_summary": "Optional. You can add this when you are ready.",
        }
        widgets = {
            "professional_summary": forms.Textarea(attrs={"rows": 5}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "mt-2 block w-full rounded-2xl border border-ink/15 bg-sand px-4 py-3 "
                "text-ink outline-none transition placeholder:text-ink/35 "
                "focus:border-coral focus:ring-2 focus:ring-coral/20"
            )

    def clean_full_name(self) -> str:
        full_name = self.cleaned_data["full_name"].strip()
        if not full_name:
            raise forms.ValidationError("Enter your full name.")
        return full_name

    def clean_timezone(self) -> str:
        timezone = self.cleaned_data["timezone"].strip()
        validate_iana_timezone(timezone)
        return timezone
