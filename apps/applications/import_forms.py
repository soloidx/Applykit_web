"""The browser-only review form for a Job Application import.

The candidate reviews and edits the proposed posting facts, confirms the
Company identity, acknowledges any duplicate warning, and explicitly saves. The
form persists nothing on its own; the save service confirms or creates the
Company and creates the single Draft Job Application atomically. The hidden
creation token makes a repeated submission of one review idempotent.
"""

from __future__ import annotations

from typing import Any

from django import forms
from django.core.exceptions import ValidationError

from apps.accounts.models import Account
from apps.applications.imports import matching_applications
from apps.applications.models import JobApplication
from apps.applications.provenance import PostingUrlRejected, sanitize_posting_url
from apps.applications.services import normalized_registrable_domain

_UNSAFE_URL_MESSAGE = (
    "Enter a safe HTTP(S) posting URL without embedded credentials, access tokens, "
    "fragments, or tracking parameters."
)


class JobApplicationImportReviewForm(forms.Form):
    company_name = forms.CharField(max_length=255, label="Company")
    company_website = forms.CharField(max_length=2048, required=False, label="Company website")
    role_title = forms.CharField(max_length=255)
    job_description = forms.CharField(widget=forms.Textarea)
    location = forms.CharField(max_length=255, required=False)
    compensation = forms.CharField(max_length=255, required=False)
    posting_url = forms.CharField(max_length=2048, required=False, label="Posting URL")
    creation_token = forms.CharField(widget=forms.HiddenInput)
    confirm_duplicate = forms.BooleanField(required=False)

    def __init__(
        self,
        *args: Any,
        account: Account,
        duplicate_matches: list[JobApplication] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.account = account
        self.duplicate_matches = list(duplicate_matches or ())
        self.fields["creation_token"].widget.attrs.pop("class", None)

    def clean_company_website(self) -> str:
        value = str(self.cleaned_data.get("company_website") or "").strip()
        if value:
            try:
                normalized_registrable_domain(value)
            except ValidationError as error:
                raise ValidationError(error.messages) from error
        return value

    def clean_posting_url(self) -> str:
        value = str(self.cleaned_data.get("posting_url") or "").strip()
        if not value:
            return ""
        try:
            return sanitize_posting_url(value)
        except PostingUrlRejected:
            raise ValidationError(_UNSAFE_URL_MESSAGE) from None

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        posting_url = str(data.get("posting_url") or "")
        if posting_url:
            self.duplicate_matches = matching_applications(
                account=self.account, posting_url=posting_url
            )
            if self.duplicate_matches and not data.get("confirm_duplicate"):
                self.add_error(
                    None,
                    "This posting URL matches an application you already have. Open the existing "
                    "application, or confirm that you want a separate one.",
                )
        return data
