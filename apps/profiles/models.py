from __future__ import annotations

from zoneinfo import available_timezones

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

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
