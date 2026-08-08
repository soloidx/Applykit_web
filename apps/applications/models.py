from django.conf import settings
from django.db import models

from apps.campaigns.models import Campaign


class Company(models.Model):
    name = models.CharField(max_length=255)
    canonical_domain = models.CharField(max_length=253, unique=True, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "companies"

    def __str__(self) -> str:
        return self.name


class CompanyDomainAlias(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="domain_aliases")
    domain = models.CharField(max_length=253, unique=True)

    def __str__(self) -> str:
        return self.domain


class JobApplication(models.Model):
    class Stage(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        INTERVIEWING = "interviewing", "Interviewing"
        OFFER = "offer", "Offer"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"

    account = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="job_applications",
    )
    campaign = models.ForeignKey(
        Campaign,
        on_delete=models.CASCADE,
        related_name="job_applications",
    )
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="job_applications")
    role_title = models.CharField(max_length=255)
    job_description = models.TextField()
    posting_url = models.URLField(blank=True)
    location = models.CharField(max_length=255, blank=True)
    compensation = models.CharField(max_length=255, blank=True)
    source = models.CharField(max_length=255, blank=True)
    private_notes = models.TextField(blank=True)
    stage = models.CharField(max_length=16, choices=Stage.choices, default=Stage.DRAFT)
    first_submitted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.role_title} at {self.company}"


class StageTransition(models.Model):
    application = models.ForeignKey(
        JobApplication,
        on_delete=models.CASCADE,
        related_name="stage_transitions",
    )
    from_stage = models.CharField(max_length=16, choices=JobApplication.Stage.choices)
    to_stage = models.CharField(max_length=16, choices=JobApplication.Stage.choices)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["occurred_at", "pk"]

    def __str__(self) -> str:
        return (
            f"{self.application}: {self.get_from_stage_display()} to {self.get_to_stage_display()}"
        )
