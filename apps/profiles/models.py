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


class Experience(models.Model):
    profile = models.ForeignKey(
        CandidateProfile,
        on_delete=models.CASCADE,
        related_name="experiences",
    )
    role = models.CharField(max_length=200)
    organization = models.CharField(max_length=200)
    location = models.CharField(max_length=200, blank=True)
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
