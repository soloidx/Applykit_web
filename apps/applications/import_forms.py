"""The browser-only review form for a Job Application import.

The candidate reviews and edits the proposed role facts, confirms an existing
shared Company, and explicitly saves. The form persists nothing on its own;
the save service creates the single Draft Job Application atomically.
"""

from __future__ import annotations

from django import forms

from apps.applications.models import Company, JobApplication


class JobApplicationImportReviewForm(forms.ModelForm):
    company = forms.ModelChoiceField(
        queryset=Company.objects.order_by("name"),
        required=True,
        label="Company",
    )

    class Meta:
        model = JobApplication
        fields = ["company", "role_title", "job_description", "location", "compensation"]
