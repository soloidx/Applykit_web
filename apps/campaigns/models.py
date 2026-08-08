from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from apps.profiles.models import validate_iana_timezone


class Campaign(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    account = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="campaigns",
    )
    weekly_target = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    monthly_target = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    timezone = models.CharField(max_length=64, validators=[validate_iana_timezone])
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(weekly_target__gt=0),
                name="campaign_weekly_target_positive",
            ),
            models.CheckConstraint(
                condition=Q(monthly_target__gt=0),
                name="campaign_monthly_target_positive",
            ),
            models.CheckConstraint(
                condition=Q(status__in=["active", "archived"]),
                name="campaign_status_valid",
            ),
            models.UniqueConstraint(
                fields=["account"],
                condition=Q(status="active"),
                name="campaign_one_active_per_account",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.account} campaign ({self.get_status_display()})"
