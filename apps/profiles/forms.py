from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

from django import forms

from apps.profiles.models import (
    IANA_TIMEZONES,
    CandidateProfile,
    Education,
    Experience,
    Highlight,
    Language,
    Project,
    validate_iana_timezone,
)
from apps.skills.models import clean_skill_label


def timezone_choices() -> list[tuple[str, str]]:
    now = datetime.now(UTC)
    choices: list[tuple[timedelta, str, str]] = []
    for timezone_name in IANA_TIMEZONES:
        offset = now.astimezone(ZoneInfo(timezone_name)).utcoffset()
        if offset is None:
            continue
        total_minutes = int(offset.total_seconds() // 60)
        sign = "+" if total_minutes >= 0 else "-"
        hours, minutes = divmod(abs(total_minutes), 60)
        label = f"UTC{sign}{hours:02d}:{minutes:02d} {timezone_name}"
        choices.append((offset, timezone_name, label))
    choices.sort(key=lambda choice: (choice[0], choice[1]))
    return [(timezone_name, label) for _, timezone_name, label in choices]


class CandidateProfileForm(forms.ModelForm):
    timezone = forms.ChoiceField(
        error_messages={"invalid_choice": "Enter a valid IANA timezone such as Europe/London."},
    )

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
            "timezone": "Choose the timezone where you plan your job search.",
            "professional_summary": "Optional. You can add this when you are ready.",
        }
        widgets = {
            "professional_summary": forms.Textarea(attrs={"rows": 5}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        timezone_field = cast(forms.ChoiceField, self.fields["timezone"])
        timezone_field.choices = [("", "Choose your timezone"), *timezone_choices()]
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


class ExperienceForm(forms.ModelForm):
    class Meta:
        model = Experience
        fields = [
            "role",
            "organization",
            "location",
            "start_date",
            "end_date",
            "description",
        ]
        labels = {
            "role": "Role",
            "organization": "Organization",
            "location": "Location",
            "start_date": "Start date",
            "end_date": "End date",
            "description": "Description",
        }
        help_texts = {
            "end_date": "Leave blank if this is your current role.",
        }
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "mt-2 block w-full rounded-2xl border border-ink/15 bg-sand px-4 py-3 "
                "text-ink outline-none transition placeholder:text-ink/35 "
                "focus:border-coral focus:ring-2 focus:ring-coral/20"
            )


class SkillAssociationForm(forms.Form):
    label = forms.CharField(max_length=200, strip=False, label="Hard skill")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["label"].widget.attrs["class"] = (
            "mt-2 block w-full rounded-2xl border border-ink/15 bg-sand px-4 py-3 "
            "text-ink outline-none transition placeholder:text-ink/35 "
            "focus:border-coral focus:ring-2 focus:ring-coral/20"
        )

    def clean_label(self) -> str:
        return clean_skill_label(self.cleaned_data["label"])


class HighlightForm(forms.ModelForm):
    class Meta:
        model = Highlight
        fields = ["text"]
        labels = {"text": "Achievement highlight"}
        widgets = {"text": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["text"].widget.attrs["class"] = (
            "mt-2 block w-full rounded-2xl border border-ink/15 bg-sand px-4 py-3 "
            "text-ink outline-none transition placeholder:text-ink/35 "
            "focus:border-coral focus:ring-2 focus:ring-coral/20"
        )


class EducationForm(forms.ModelForm):
    class Meta:
        model = Education
        fields = ["institution", "degree", "start_date", "end_date"]
        labels = {
            "institution": "Institution",
            "degree": "Degree",
            "start_date": "Start date",
            "end_date": "End date",
        }
        help_texts = {"end_date": "Leave blank if this is ongoing."}
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "mt-2 block w-full rounded-2xl border border-ink/15 bg-sand px-4 py-3 "
                "text-ink outline-none transition placeholder:text-ink/35 "
                "focus:border-coral focus:ring-2 focus:ring-coral/20"
            )


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ["name", "description", "url"]
        labels = {
            "name": "Project name",
            "description": "Description",
            "url": "Project URL",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "mt-2 block w-full rounded-2xl border border-ink/15 bg-sand px-4 py-3 "
                "text-ink outline-none transition placeholder:text-ink/35 "
                "focus:border-coral focus:ring-2 focus:ring-coral/20"
            )


class LanguageForm(forms.ModelForm):
    name = forms.CharField(strip=False, label="Language")

    class Meta:
        model = Language
        fields = ["name", "proficiency"]
        labels = {"name": "Language", "proficiency": "Proficiency"}

    def __init__(
        self,
        *args: Any,
        profile: CandidateProfile | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.profile = profile or getattr(self.instance, "profile", None)
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "mt-2 block w-full rounded-2xl border border-ink/15 bg-sand px-4 py-3 "
                "text-ink outline-none transition placeholder:text-ink/35 "
                "focus:border-coral focus:ring-2 focus:ring-coral/20"
            )

    def clean_name(self) -> str:
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Enter a language.")
        if (
            self.profile
            and Language.objects.filter(
                profile=self.profile,
                normalized_name=name.casefold(),
            )
            .exclude(pk=self.instance.pk)
            .exists()
        ):
            raise forms.ValidationError("This language is already in your profile.")
        return name
