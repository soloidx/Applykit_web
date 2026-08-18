from __future__ import annotations

from html import escape

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import Account
from apps.applications.models import JobApplication
from apps.cover_letters.content import cover_letter_visible_text, sanitize_cover_letter_html
from apps.cover_letters.models import CoverLetter
from apps.profiles.models import CandidateProfile


def _locked_application(*, account: Account, application_id: int) -> JobApplication:
    return JobApplication.objects.select_for_update().get(
        pk=application_id,
        account=account,
    )


def build_cover_letter_starter_template(
    *, application: JobApplication, profile: CandidateProfile
) -> str:
    role_title = escape(application.role_title, quote=False)
    company_name = escape(application.company.name, quote=False)
    full_name = escape(profile.full_name, quote=False)
    return (
        "<p>Dear Hiring Team,</p>"
        f"<p>I am writing to apply for the {role_title} position at {company_name}.</p>"
        "<p>[Describe one or two relevant experiences, achievements, or skills "
        "that address this role.]</p>"
        f"<p>[Explain why this role and {company_name} interest you.]</p>"
        "<p>Thank you for your time and consideration.</p>"
        f"<p>Sincerely,<br>{full_name}</p>"
    )


@transaction.atomic
def save_cover_letter(*, account: Account, application_id: int, body_html: str) -> CoverLetter:
    application = _locked_application(account=account, application_id=application_id)
    canonical_html = sanitize_cover_letter_html(body_html)
    if not cover_letter_visible_text(canonical_html).strip():
        raise ValidationError("Cover Letter must contain visible text.")

    cover_letter = CoverLetter.objects.select_for_update().filter(application=application).first()
    if cover_letter is None:
        return CoverLetter.objects.create(application=application, body_html=canonical_html)
    cover_letter.body_html = canonical_html
    cover_letter.save(update_fields=["body_html", "updated_at"])
    return cover_letter


@transaction.atomic
def delete_cover_letter(*, account: Account, application_id: int) -> None:
    application = _locked_application(account=account, application_id=application_id)
    cover_letter = CoverLetter.objects.select_for_update().get(application=application)
    cover_letter.delete()
