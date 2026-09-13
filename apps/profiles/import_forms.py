"""The browser-only review form for a Candidate Profile import.

The candidate reviews and edits the proposed core facts and then explicitly
confirms the full name before the save request. The form carries the opaque
version token that binds the draft to the reviewed profile state.
"""

from __future__ import annotations

from typing import Any

from django import forms

from apps.profiles.forms import CandidateProfileForm


class ProfileImportReviewForm(CandidateProfileForm):
    confirm_full_name = forms.BooleanField(
        required=True,
        label="I confirm this is my full name",
        error_messages={"required": "Confirm your full name before saving."},
    )
    version_token = forms.CharField(widget=forms.HiddenInput)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["confirm_full_name"].widget.attrs["class"] = (
            "size-4 rounded border-ink/25 text-coral focus:ring-coral/30"
        )
        self.fields["version_token"].widget.attrs.pop("class", None)
