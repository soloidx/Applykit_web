import pytest
from allauth.account.models import EmailAddress
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Account
from apps.applications.models import (
    ApplicationSkillRequirement,
    Company,
    CompanyDomainAlias,
    JobApplication,
)
from apps.campaigns.models import Campaign
from apps.cover_letters.models import CoverLetter
from apps.profiles.models import (
    CandidateProfile,
    Education,
    Experience,
    Highlight,
    Language,
    ProfileSkill,
    Project,
)
from apps.resumes.forms import build_resume_forms
from apps.resumes.models import (
    Resume,
    ResumeEducation,
    ResumeExperience,
    ResumeExperienceHighlight,
    ResumeLanguage,
    ResumeProject,
    ResumeSection,
    ResumeSkill,
)
from apps.resumes.services import open_resume
from apps.skills.models import SkillConcept

pytestmark = pytest.mark.integration

DESTRUCTIVE_HEADER = "This permanently deletes the Job Application and cannot be undone."


def verified_candidate(email: str) -> Account:
    account = Account.objects.create_user(email, "a-secure-password")
    EmailAddress.objects.create(user=account, email=account.email, primary=True, verified=True)
    CandidateProfile.objects.create(
        account=account,
        full_name="Ada Lovelace",
        timezone="Europe/London",
    )
    Campaign.objects.create(
        account=account,
        weekly_target=5,
        monthly_target=20,
        timezone="Europe/London",
    )
    return account


def application_for(account: Account, role_title: str = "Platform engineer") -> JobApplication:
    return JobApplication.objects.create(
        account=account,
        campaign=Campaign.objects.get(account=account),
        company=Company.objects.create(name=f"{role_title} Company"),
        role_title=role_title,
        job_description="Build dependable internal systems.",
    )


def company_with_aliases() -> tuple[Company, CompanyDomainAlias]:
    company = Company.objects.create(name="Example Careers", canonical_domain="example.com")
    alias = CompanyDomainAlias.objects.create(company=company, domain="example.org")
    return company, alias


def create_document_sources(*, account: Account) -> SkillConcept:
    profile = CandidateProfile.objects.get(account=account)
    experience = Experience.objects.create(
        profile=profile,
        role="Senior engineer",
        organization="Analytical Engines Ltd",
        location="London",
        start_date="2020-01-01",
    )
    Highlight.objects.create(experience=experience, text="Built reliable systems.")
    Education.objects.create(
        profile=profile,
        institution="University of London",
        degree="Mathematics",
        start_date="2015-01-01",
    )
    Project.objects.create(profile=profile, name="Analytical engine simulator")
    Language.objects.create(
        profile=profile,
        name="English",
        proficiency=Language.Proficiency.NATIVE,
    )
    concept = SkillConcept.objects.create(canonical_name="Python")
    ProfileSkill.objects.create(profile=profile, concept=concept, label="Python")
    return concept


def resume_payload(resume: Resume) -> dict[str, object]:
    draft_forms = build_resume_forms(resume=resume)
    data: dict[str, object] = {}
    for form_name, form in (("header", draft_forms.header),):
        for field_name in form.fields:
            value = form.initial.get(field_name, "")
            if isinstance(value, bool):
                if value:
                    data[f"{form_name}-{field_name}"] = "on"
            else:
                data[f"{form_name}-{field_name}"] = value if value is not None else ""
    for prefix, formset in (
        ("sections", draft_forms.sections),
        ("experiences", draft_forms.experiences),
        ("highlights", draft_forms.highlights),
        ("projects", draft_forms.projects),
        ("educations", draft_forms.educations),
        ("languages", draft_forms.languages),
        ("skills", draft_forms.skills),
    ):
        data[f"{prefix}-TOTAL_FORMS"] = str(len(formset.forms))
        data[f"{prefix}-INITIAL_FORMS"] = str(len(formset.forms))
        data[f"{prefix}-MIN_NUM_FORMS"] = "0"
        data[f"{prefix}-MAX_NUM_FORMS"] = str(len(formset.forms))
        for index, form in enumerate(formset.forms):
            for field_name in form.fields:
                value = form.initial.get(field_name, "")
                if isinstance(value, bool):
                    if value:
                        data[f"{prefix}-{index}-{field_name}"] = "on"
                else:
                    data[f"{prefix}-{index}-{field_name}"] = value if value is not None else ""
    return data


def open_and_save_documents(
    *, client: Client, application: JobApplication, full_name: str
) -> Resume:
    assert client.get(reverse("resume_detail", args=[application.pk])).status_code == 200
    resume = Resume.objects.get(application=application)
    payload = resume_payload(resume)
    payload["header-full_name"] = full_name
    payload.pop("header-full_name_inherit", None)
    assert client.post(reverse("resume_save", args=[application.pk]), payload).status_code == 302
    assert (
        client.post(
            reverse("cover_letter_save", args=[application.pk]),
            {"body_html": "<p>A saved letter.</p>"},
        ).status_code
        == 302
    )
    return resume


@pytest.mark.django_db
def test_application_delete_confirmation_explicitly_names_document_loss() -> None:
    account = verified_candidate("app-delete-confirm@example.com")
    create_document_sources(account=account)
    application = application_for(account)
    plain_application = application_for(account, "Plain engineer")
    client = Client()
    client.force_login(account)

    open_and_save_documents(client=client, application=application, full_name="Tailored Ada")

    confirmation = client.get(reverse("application_delete", args=[application.pk]))
    plain_confirmation = client.get(reverse("application_delete", args=[plain_application.pk]))

    for page in (confirmation, plain_confirmation):
        assert page.status_code == 200
        assert DESTRUCTIVE_HEADER.encode() in page.content
        assert b"tailored Resume content" in page.content
        assert b"saved Cover Letter" in page.content


@pytest.mark.django_db
def test_confirmed_application_deletion_cascades_documents_and_preserves_shared_records() -> None:
    account = verified_candidate("app-delete-cascade@example.com")
    concept = create_document_sources(account=account)
    company, alias = company_with_aliases()
    application = JobApplication.objects.create(
        account=account,
        campaign=Campaign.objects.get(account=account),
        company=company,
        role_title="Platform engineer",
        job_description="Build dependable internal systems.",
        private_notes="Private preparation.",
    )
    client = Client()
    client.force_login(account)

    resume = open_and_save_documents(
        client=client,
        application=application,
        full_name="Tailored Ada",
    )
    highlights_index = next(
        index
        for index, form in enumerate(build_resume_forms(resume=resume).highlights.forms)
        if form.initial["included"]
    )
    payload = resume_payload(resume)
    payload[f"highlights-{highlights_index}-text_override"] = "A tailored highlight."
    payload.pop(f"highlights-{highlights_index}-text_override_inherit", None)
    assert client.post(reverse("resume_save", args=[application.pk]), payload).status_code == 302
    resume_id = resume.pk
    application_id = application.pk
    assert ResumeExperienceHighlight.objects.filter(resume_experience__resume_id=resume_id).exists()

    deleted = client.post(
        reverse("application_delete", args=[application.pk]),
        {"confirm": "1"},
        headers={"HX-Request": "true"},
    )

    assert deleted.status_code == 200
    assert deleted.headers["HX-Redirect"] == reverse("dashboard")
    assert not JobApplication.objects.filter(pk=application_id).exists()
    assert not Resume.objects.filter(pk=resume_id).exists()
    assert not ResumeSection.objects.filter(resume_id=resume_id).exists()
    assert not ResumeExperience.objects.filter(resume_id=resume_id).exists()
    assert not ResumeExperienceHighlight.objects.filter(
        resume_experience__resume_id=resume_id
    ).exists()
    assert not ResumeProject.objects.filter(resume_id=resume_id).exists()
    assert not ResumeEducation.objects.filter(resume_id=resume_id).exists()
    assert not ResumeLanguage.objects.filter(resume_id=resume_id).exists()
    assert not ResumeSkill.objects.filter(resume_id=resume_id).exists()
    assert not CoverLetter.objects.filter(application_id=application_id).exists()
    assert Company.objects.filter(pk=company.pk, name="Example Careers").exists()
    assert CompanyDomainAlias.objects.filter(pk=alias.pk, company=company).exists()
    assert SkillConcept.objects.filter(pk=concept.pk).exists()
    assert CandidateProfile.objects.filter(account=account).exists()


@pytest.mark.django_db
def test_account_delete_confirmation_names_resume_and_cover_letter_data() -> None:
    account = verified_candidate("account-delete-confirm@example.com")
    client = Client()
    client.force_login(account)

    confirmation = client.get(reverse("account_delete"))

    assert confirmation.status_code == 200
    assert b"tailored Resumes" in confirmation.content
    assert b"Cover Letters" in confirmation.content


@pytest.mark.django_db
def test_confirmed_account_deletion_removes_documents_and_preserves_shared_catalog() -> None:
    account = verified_candidate("account-delete-cascade@example.com")
    other_account = verified_candidate("account-delete-other@example.com")
    concept = create_document_sources(account=account)
    company, alias = company_with_aliases()
    application = JobApplication.objects.create(
        account=account,
        campaign=Campaign.objects.get(account=account),
        company=company,
        role_title="Platform engineer",
        job_description="Build dependable internal systems.",
        private_notes="Private preparation.",
    )
    retained = JobApplication.objects.create(
        account=other_account,
        campaign=Campaign.objects.get(account=other_account),
        company=company,
        role_title="Other candidate role",
        job_description="Other candidate description.",
        private_notes="Other candidate private note.",
    )
    ApplicationSkillRequirement.objects.create(
        application=retained,
        concept=concept,
        label="Python",
        classification=ApplicationSkillRequirement.Classification.REQUIRED,
    )
    resume, _created = open_resume(account=account, application_id=application.pk)
    resume.full_name_override = "Tailored Ada"
    resume.save(update_fields=["full_name_override"])
    CoverLetter.objects.create(application=application, body_html="<p>A saved letter.</p>")
    resume_id = resume.pk
    application_id = application.pk
    account_id = account.pk
    client = Client()
    client.force_login(account)

    response = client.post(reverse("account_delete"), {"confirm": "1"})

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("home")
    assert not Account.objects.filter(pk=account_id).exists()
    assert not CandidateProfile.objects.filter(account_id=account_id).exists()
    assert not JobApplication.objects.filter(pk=application_id).exists()
    assert not Resume.objects.filter(pk=resume_id).exists()
    assert not ResumeSection.objects.filter(resume_id=resume_id).exists()
    assert not ResumeExperience.objects.filter(resume_id=resume_id).exists()
    assert not ResumeProject.objects.filter(resume_id=resume_id).exists()
    assert not ResumeEducation.objects.filter(resume_id=resume_id).exists()
    assert not ResumeLanguage.objects.filter(resume_id=resume_id).exists()
    assert not ResumeSkill.objects.filter(resume_id=resume_id).exists()
    assert not CoverLetter.objects.filter(application_id=application_id).exists()
    assert Company.objects.filter(pk=company.pk, name="Example Careers").exists()
    assert CompanyDomainAlias.objects.filter(pk=alias.pk, company=company).exists()
    assert SkillConcept.objects.filter(pk=concept.pk).exists()
    retained.refresh_from_db()
    assert retained.account_id == other_account.pk
    assert retained.company_id == company.pk
    assert retained.private_notes == "Other candidate private note."
    retained_requirement = ApplicationSkillRequirement.objects.get(application=retained)
    assert retained_requirement.concept_id == concept.pk


@pytest.mark.django_db
def test_document_routes_give_another_account_404_without_existence_indicators() -> None:
    owner = verified_candidate("document-owner@example.com")
    intruder = verified_candidate("document-intruder@example.com")
    application = application_for(owner)
    resume, _created = open_resume(account=owner, application_id=application.pk)
    resume.full_name_override = "Tailored Ada Lovelace"
    resume.save(update_fields=["full_name_override"])
    CoverLetter.objects.create(
        application=application,
        body_html="<p>Private letter body.</p>",
    )
    client = Client()
    client.force_login(intruder)

    resume_detail = client.get(reverse("resume_detail", args=[application.pk]))
    resume_save = client.post(reverse("resume_save", args=[application.pk]), {})
    cover_detail = client.get(reverse("cover_letter_detail", args=[application.pk]))
    cover_save = client.post(
        reverse("cover_letter_save", args=[application.pk]),
        {"body_html": "<p>Forged letter.</p>"},
    )
    cover_delete = client.post(reverse("cover_letter_delete", args=[application.pk]))

    for response in (resume_detail, resume_save, cover_detail, cover_save, cover_delete):
        assert response.status_code == 404
        assert b"Tailored Ada Lovelace" not in response.content
        assert b"Private letter body." not in response.content
    resume.refresh_from_db()
    assert resume.full_name_override == "Tailored Ada Lovelace"
    assert (
        CoverLetter.objects.get(application=application).body_html == "<p>Private letter body.</p>"
    )


@pytest.mark.django_db
def test_duplicate_applications_keep_independent_document_aggregates() -> None:
    account = verified_candidate("duplicate-documents@example.com")
    first = application_for(account, "Platform engineer")
    second = application_for(account, "Platform engineer")
    client = Client()
    client.force_login(account)

    assert client.get(reverse("resume_detail", args=[first.pk])).status_code == 200
    first_resume = Resume.objects.get(application=first)
    assert not Resume.objects.filter(application=second).exists()

    payload = resume_payload(first_resume)
    payload["header-full_name"] = "First attempt tailoring"
    payload.pop("header-full_name_inherit", None)
    assert client.post(reverse("resume_save", args=[first.pk]), payload).status_code == 302
    assert (
        client.post(
            reverse("cover_letter_save", args=[first.pk]),
            {"body_html": "<p>First letter.</p>"},
        ).status_code
        == 302
    )

    assert client.get(reverse("resume_detail", args=[second.pk])).status_code == 200
    second_resume = Resume.objects.get(application=second)
    assert second_resume.pk != first_resume.pk
    assert second_resume.full_name_override is None
    assert not CoverLetter.objects.filter(application=second).exists()

    second_payload = resume_payload(second_resume)
    second_payload["header-full_name"] = "Second attempt tailoring"
    second_payload.pop("header-full_name_inherit", None)
    assert client.post(reverse("resume_save", args=[second.pk]), second_payload).status_code == 302
    assert (
        client.post(
            reverse("cover_letter_save", args=[second.pk]),
            {"body_html": "<p>Second letter.</p>"},
        ).status_code
        == 302
    )

    first_resume.refresh_from_db()
    second_resume.refresh_from_db()
    assert first_resume.full_name_override == "First attempt tailoring"
    assert second_resume.full_name_override == "Second attempt tailoring"
    assert Resume.objects.filter(application__account=account).count() == 2
    assert CoverLetter.objects.filter(application__account=account).count() == 2
    assert CoverLetter.objects.get(application=first).body_html == "<p>First letter.</p>"
    assert CoverLetter.objects.get(application=second).body_html == "<p>Second letter.</p>"

    first_detail = client.get(reverse("resume_detail", args=[first.pk]))
    assert b"First attempt tailoring" in first_detail.content
    assert b"Second attempt tailoring" not in first_detail.content


@pytest.mark.django_db
def test_documents_are_editable_at_every_stage_and_not_required_for_submitted() -> None:
    account = verified_candidate("stage-documents@example.com")
    application = application_for(account)
    client = Client()
    client.force_login(account)

    submitted = client.post(
        reverse("application_detail", args=[application.pk]),
        {"stage": JobApplication.Stage.SUBMITTED},
    )

    assert submitted.status_code == 302
    assert not Resume.objects.filter(application=application).exists()
    assert not CoverLetter.objects.filter(application=application).exists()
    application.refresh_from_db()
    assert application.stage == JobApplication.Stage.SUBMITTED

    assert client.get(reverse("resume_detail", args=[application.pk])).status_code == 200
    resume = Resume.objects.get(application=application)
    payload = resume_payload(resume)
    payload["header-full_name"] = "Tailored at Submitted"
    payload.pop("header-full_name_inherit", None)
    assert client.post(reverse("resume_save", args=[application.pk]), payload).status_code == 302

    for stage in (
        JobApplication.Stage.INTERVIEWING,
        JobApplication.Stage.OFFER,
        JobApplication.Stage.ACCEPTED,
        JobApplication.Stage.REJECTED,
        JobApplication.Stage.WITHDRAWN,
    ):
        transitioned = client.post(
            reverse("application_detail", args=[application.pk]),
            {"stage": stage},
        )
        assert transitioned.status_code == 302
        application.refresh_from_db()
        assert application.stage == stage
        assert client.get(reverse("resume_detail", args=[application.pk])).status_code == 200
        assert client.get(reverse("cover_letter_detail", args=[application.pk])).status_code == 200
        resume_payload_for_stage = resume_payload(Resume.objects.get(application=application))
        assert (
            client.post(
                reverse("resume_save", args=[application.pk]), resume_payload_for_stage
            ).status_code
            == 302
        )
        assert (
            client.post(
                reverse("cover_letter_save", args=[application.pk]),
                {"body_html": f"<p>Letter at {stage.value}.</p>"},
            ).status_code
            == 302
        )

    application.refresh_from_db()
    assert application.stage == JobApplication.Stage.WITHDRAWN
    cover_letter = CoverLetter.objects.get(application=application)
    assert cover_letter.body_html == f"<p>Letter at {JobApplication.Stage.WITHDRAWN.value}.</p>"
