from __future__ import annotations

from typing import Any
from zoneinfo import available_timezones

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q

IANA_TIMEZONES = frozenset(available_timezones())


def validate_iana_timezone(value: str) -> None:
    if value not in IANA_TIMEZONES:
        raise ValidationError("Enter a valid IANA timezone such as Europe/London.")


class CandidateProfile(models.Model):
    account = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="candidate_profile",
    )
    full_name = models.CharField(max_length=200)
    timezone = models.CharField(max_length=64, validators=[validate_iana_timezone])
    professional_title = models.CharField(max_length=200, blank=True)
    professional_summary = models.TextField(blank=True)
    phone_number = models.CharField(max_length=32, blank=True)
    location = models.CharField(max_length=200, blank=True)
    linkedin_url = models.URLField(blank=True)
    portfolio_url = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["full_name"]

    def __str__(self) -> str:
        return self.full_name

    @property
    def has_minimum_details(self) -> bool:
        return bool(self.full_name.strip()) and self.timezone in IANA_TIMEZONES


class PresentationPreferences(models.Model):
    profile = models.OneToOneField(
        CandidateProfile,
        on_delete=models.CASCADE,
        related_name="presentation_preferences",
    )
    show_contact_details = models.BooleanField(default=True)
    show_professional_summary = models.BooleanField(default=True)
    show_experience = models.BooleanField(default=True)
    show_education = models.BooleanField(default=True)
    show_projects = models.BooleanField(default=True)
    show_skills = models.BooleanField(default=True)
    show_languages = models.BooleanField(default=True)

    def __str__(self) -> str:
        return f"Presentation preferences for {self.profile}"


class Experience(models.Model):
    profile = models.ForeignKey(
        CandidateProfile,
        on_delete=models.CASCADE,
        related_name="experiences",
    )
    role = models.CharField(max_length=200)
    organization = models.CharField(max_length=200)
    location = models.CharField(max_length=200)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")),
                name="experience_end_date_on_or_after_start",
            )
        ]

    def __str__(self) -> str:
        return f"{self.role} at {self.organization}"

    def clean(self) -> None:
        super().clean()
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date must be on or after the start date."})

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self._state.adding and self.position == 0:
            last_experience = (
                Experience.objects.filter(profile=self.profile).order_by("-position", "-id").first()
            )
            self.position = last_experience.position + 1 if last_experience else 0
        super().save(*args, **kwargs)


class Highlight(models.Model):
    experience = models.ForeignKey(
        Experience,
        on_delete=models.CASCADE,
        related_name="highlights",
    )
    text = models.TextField()
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self) -> str:
        return self.text

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self._state.adding and self.position == 0:
            last_highlight = (
                Highlight.objects.filter(experience=self.experience)
                .order_by("-position", "-id")
                .first()
            )
            self.position = last_highlight.position + 1 if last_highlight else 0
        super().save(*args, **kwargs)


class Education(models.Model):
    profile = models.ForeignKey(
        CandidateProfile,
        on_delete=models.CASCADE,
        related_name="educations",
    )
    institution = models.CharField(max_length=200)
    degree = models.CharField(max_length=200)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")),
                name="education_end_date_on_or_after_start",
            )
        ]

    def __str__(self) -> str:
        return f"{self.degree} at {self.institution}"

    def clean(self) -> None:
        super().clean()
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date must be on or after the start date."})

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self._state.adding and self.position == 0:
            last_education = (
                Education.objects.filter(profile=self.profile).order_by("-position", "-id").first()
            )
            self.position = last_education.position + 1 if last_education else 0
        super().save(*args, **kwargs)


class Project(models.Model):
    profile = models.ForeignKey(
        CandidateProfile,
        on_delete=models.CASCADE,
        related_name="projects",
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    technologies = models.TextField(blank=True)
    url = models.URLField(blank=True)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self._state.adding and self.position == 0:
            last_project = (
                Project.objects.filter(profile=self.profile).order_by("-position", "-id").first()
            )
            self.position = last_project.position + 1 if last_project else 0
        super().save(*args, **kwargs)


class Skill(models.Model):
    profile = models.ForeignKey(
        CandidateProfile,
        on_delete=models.CASCADE,
        related_name="skills",
    )
    name = models.CharField(max_length=100)
    normalized_name = models.CharField(max_length=100, editable=False)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "normalized_name"],
                name="skill_unique_name_per_profile",
            )
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.name = self.name.strip()
        self.normalized_name = self.name.casefold()
        if self._state.adding and self.position == 0:
            last_skill = (
                Skill.objects.filter(profile=self.profile).order_by("-position", "-id").first()
            )
            self.position = last_skill.position + 1 if last_skill else 0
        super().save(*args, **kwargs)


class Language(models.Model):
    class Proficiency(models.TextChoices):
        BEGINNER = "beginner", "Beginner"
        INTERMEDIATE = "intermediate", "Intermediate"
        ADVANCED = "advanced", "Advanced"
        FLUENT = "fluent", "Fluent"
        NATIVE = "native", "Native"

    profile = models.ForeignKey(
        CandidateProfile,
        on_delete=models.CASCADE,
        related_name="languages",
    )
    name = models.CharField(max_length=100)
    normalized_name = models.CharField(max_length=100, editable=False)
    proficiency = models.CharField(max_length=20, choices=Proficiency.choices)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "normalized_name"],
                name="language_unique_name_per_profile",
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_proficiency_display()})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.name = self.name.strip()
        self.normalized_name = self.name.casefold()
        if self._state.adding and self.position == 0:
            last_language = (
                Language.objects.filter(profile=self.profile).order_by("-position", "-id").first()
            )
            self.position = last_language.position + 1 if last_language else 0
        super().save(*args, **kwargs)
