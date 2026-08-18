from datetime import UTC, datetime
from typing import cast
from zoneinfo import ZoneInfo

import pytest
from allauth.account.models import EmailAddress
from django import forms
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Account
from apps.profiles.forms import CandidateProfileForm
from apps.profiles.models import (
    IANA_TIMEZONES,
    CandidateProfile,
    Education,
    Experience,
    ExperienceSkill,
    Highlight,
    Language,
    ProfileSkill,
    Project,
    ProjectSkill,
)
from apps.profiles.services import (
    create_experience_skill,
    create_profile_skill,
    create_project_skill,
    delete_experience_skill,
    delete_profile_skill,
    delete_project_skill,
)
from apps.skills.models import SkillAlias, SkillConcept
from apps.skills.services import resolve_skill_label

pytestmark = pytest.mark.integration


def verified_account(email: str) -> Account:
    account = Account.objects.create_user(email, "a-secure-password")
    EmailAddress.objects.create(user=account, email=account.email, primary=True, verified=True)
    return account


def profile_data(**overrides: str) -> dict[str, str]:
    data = {
        "full_name": "Ada Lovelace",
        "timezone": "Europe/London",
        "contact_email": "contact@example.com",
        "professional_title": "Analytical engine specialist",
        "professional_summary": "I make complex systems easier to understand.",
        "phone_number": "+44 20 7946 0958",
        "location": "London, UK",
        "linkedin_url": "https://www.linkedin.com/in/ada-lovelace",
        "portfolio_url": "https://ada.example.com",
    }
    data.update(overrides)
    return data


@pytest.mark.django_db
def test_anonymous_user_is_sent_to_sign_in_from_profile_onboarding() -> None:
    response = Client().get(reverse("profile"))

    assert response.status_code == 302
    assert response.url == f"{reverse('account_login')}?next={reverse('profile')}"


@pytest.mark.django_db
def test_verified_account_is_sent_to_profile_before_private_dashboard() -> None:
    account = verified_account("candidate@example.com")
    client = Client()
    client.force_login(account)

    response = client.get(reverse("dashboard"))

    assert response.status_code == 302
    assert response.url == reverse("profile")
    assert CandidateProfile.objects.filter(account=account).exists() is False


@pytest.mark.django_db
def test_new_profile_contact_email_starts_with_account_email() -> None:
    account = verified_account("candidate@example.com")
    client = Client()
    client.force_login(account)

    response = client.get(reverse("profile"))

    assert response.status_code == 200
    assert response.context["form"]["contact_email"].value() == account.email


@pytest.mark.django_db
def test_profile_contact_email_is_required_editable_and_independent_from_account_email() -> None:
    account = verified_account("candidate@example.com")
    client = Client()
    client.force_login(account)

    created = client.post(
        reverse("profile"),
        profile_data(contact_email="documents@example.com"),
    )

    assert created.status_code == 302
    profile = CandidateProfile.objects.get(account=account)
    assert profile.contact_email == "documents@example.com"

    account.email = "new-sign-in@example.com"
    account.save(update_fields=["email"])

    updated = client.post(
        reverse("profile"),
        profile_data(contact_email="updated-documents@example.com"),
    )

    assert updated.status_code == 302
    profile.refresh_from_db()
    assert profile.contact_email == "updated-documents@example.com"


@pytest.mark.django_db
def test_profile_contact_email_is_required() -> None:
    account = verified_account("candidate@example.com")
    client = Client()
    client.force_login(account)

    response = client.post(reverse("profile"), profile_data(contact_email=""))

    assert response.status_code == 200
    assert b"This field is required." in response.content
    assert CandidateProfile.objects.filter(account=account).exists() is False


@pytest.mark.django_db
def test_profile_requires_full_name_and_valid_iana_timezone() -> None:
    account = verified_account("candidate@example.com")
    client = Client()
    client.force_login(account)

    response = client.post(
        reverse("profile"),
        {"full_name": "", "timezone": "not/a-timezone"},
    )

    assert response.status_code == 200
    assert b"This field is required." in response.content
    assert b"Enter a valid IANA timezone" in response.content
    assert CandidateProfile.objects.filter(account=account).exists() is False


def test_profile_timezone_is_a_current_offset_sorted_iana_timezone_select() -> None:
    form = CandidateProfileForm()
    timezone_field = cast(forms.ChoiceField, form.fields["timezone"])
    choices = list(timezone_field.choices)

    assert timezone_field.widget.input_type == "select"
    assert choices[0] == ("", "Choose your timezone")
    assert {value for value, _ in choices[1:]} == IANA_TIMEZONES
    assert all(label.startswith("UTC+") or label.startswith("UTC-") for _, label in choices[1:])
    assert all(" " in label for _, label in choices[1:])
    assert ("America/Lima", "UTC-05:00 America/Lima") in choices
    assert ("Asia/Kathmandu", "UTC+05:45 Asia/Kathmandu") in choices
    expected_values = sorted(
        IANA_TIMEZONES,
        key=lambda timezone_name: (
            datetime.now(UTC).astimezone(ZoneInfo(timezone_name)).utcoffset(),
            timezone_name,
        ),
    )
    assert [value for value, _ in choices[1:]] == expected_values


@pytest.mark.django_db
def test_profile_renders_timezone_select_with_the_stored_value_selected() -> None:
    account = verified_account("timezone-select@example.com")
    CandidateProfile.objects.create(
        account=account,
        full_name="Ada Lovelace",
        timezone="America/Lima",
    )
    client = Client()
    client.force_login(account)

    response = client.get(reverse("profile"))

    assert b'<select name="timezone"' in response.content
    assert b'<option value="America/Lima" selected>' in response.content


@pytest.mark.django_db
def test_minimum_profile_completion_unlocks_dashboard() -> None:
    account = verified_account("candidate@example.com")
    client = Client()
    client.force_login(account)

    response = client.post(reverse("profile"), profile_data())

    assert response.status_code == 302
    assert response.url == reverse("dashboard")
    profile = CandidateProfile.objects.get(account=account)
    assert profile.full_name == "Ada Lovelace"
    assert profile.timezone == "Europe/London"
    assert client.get(reverse("dashboard")).status_code == 200


@pytest.mark.django_db
def test_optional_profile_details_can_be_added_progressively() -> None:
    account = verified_account("candidate@example.com")
    client = Client()
    client.force_login(account)
    client.post(reverse("profile"), profile_data())

    response = client.post(
        reverse("profile"),
        profile_data(
            professional_title="Principal systems designer",
            professional_summary="A refreshed summary.",
            phone_number="",
            location="Cambridge, UK",
            linkedin_url="",
            portfolio_url="",
        ),
    )

    assert response.status_code == 302
    profile = CandidateProfile.objects.get(account=account)
    assert profile.professional_title == "Principal systems designer"
    assert profile.professional_summary == "A refreshed summary."
    assert profile.phone_number == ""
    assert profile.location == "Cambridge, UK"


@pytest.mark.django_db
def test_htmx_profile_submission_has_the_same_persisted_result_and_validation_errors() -> None:
    account = verified_account("candidate@example.com")
    client = Client()
    client.force_login(account)

    invalid_response = client.post(
        reverse("profile"),
        {"full_name": "Ada Lovelace", "timezone": "Mars/Colony"},
        headers={"HX-Request": "true"},
    )
    valid_response = client.post(
        reverse("profile"),
        profile_data(),
        headers={"HX-Request": "true"},
    )

    assert invalid_response.status_code == 200
    assert b"Enter a valid IANA timezone" in invalid_response.content
    assert valid_response.status_code == 200
    assert b"Profile saved" in valid_response.content
    assert CandidateProfile.objects.filter(
        account=account,
        full_name="Ada Lovelace",
        timezone="Europe/London",
    ).exists()


@pytest.mark.django_db
def test_account_can_own_at_most_one_candidate_profile() -> None:
    account = verified_account("candidate@example.com")
    CandidateProfile.objects.create(account=account, full_name="Ada Lovelace", timezone="UTC")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CandidateProfile.objects.create(
                account=account, full_name="Another Name", timezone="UTC"
            )


@pytest.mark.django_db
def test_profile_reads_and_writes_are_isolated_by_authenticated_account() -> None:
    first = verified_account("first@example.com")
    second = verified_account("second@example.com")
    CandidateProfile.objects.create(account=first, full_name="First Candidate", timezone="UTC")
    CandidateProfile.objects.create(account=second, full_name="Second Candidate", timezone="UTC")
    client = Client()
    client.force_login(first)

    response = client.get(reverse("profile"))
    client.post(reverse("profile"), profile_data(full_name="Updated First Candidate"))

    assert response.status_code == 200
    assert b"First Candidate" in response.content
    assert b"Second Candidate" not in response.content
    assert CandidateProfile.objects.get(account=second).full_name == "Second Candidate"


@pytest.mark.django_db
def test_unverified_account_cannot_access_profile_onboarding() -> None:
    account = Account.objects.create_user("candidate@example.com", "a-secure-password")
    EmailAddress.objects.create(user=account, email=account.email, primary=True, verified=False)
    client = Client()
    client.force_login(account)

    response = client.get(reverse("profile"))

    assert response.status_code == 302
    assert response.url == reverse("account_login")


def experience_data(**overrides: str) -> dict[str, str]:
    data = {
        "role": "Senior engineer",
        "organization": "Analytical Engines Ltd",
        "location": "London, UK",
        "start_date": "2020-01-01",
        "end_date": "2023-06-30",
        "description": "Built reliable systems.",
    }
    data.update(overrides)
    return data


def create_profile(account: Account) -> CandidateProfile:
    return CandidateProfile.objects.create(
        account=account,
        full_name="Ada Lovelace",
        timezone="Europe/London",
    )


@pytest.mark.django_db
def test_skill_association_services_are_account_scoped_and_normalize_after_delete() -> None:
    owner = verified_account("profile-service-owner@example.com")
    intruder = verified_account("profile-service-intruder@example.com")
    profile = create_profile(owner)
    create_profile(intruder)
    experience = Experience.objects.create(
        profile=profile,
        role="Engineer",
        organization="Example",
        location="London",
        start_date="2020-01-01",
    )
    project = Project.objects.create(profile=profile, name="Toolkit")

    profile_skill = create_profile_skill(account=owner, label=" Python ")
    experience_skill = create_experience_skill(
        account=owner,
        experience_id=experience.pk,
        label="Django",
    )
    project_skill = create_project_skill(
        account=owner,
        project_id=project.pk,
        label="Rust",
    )

    assert profile_skill.label == "Python"
    assert profile_skill.position == 0
    assert experience_skill.position == 0
    assert project_skill.position == 0

    with pytest.raises(ProfileSkill.DoesNotExist):
        delete_profile_skill(account=intruder, skill_id=profile_skill.pk)
    with pytest.raises(ExperienceSkill.DoesNotExist):
        delete_experience_skill(account=intruder, experience_skill_id=experience_skill.pk)
    with pytest.raises(ProjectSkill.DoesNotExist):
        delete_project_skill(account=intruder, project_skill_id=project_skill.pk)

    second_profile_skill = create_profile_skill(account=owner, label="TypeScript")
    delete_profile_skill(account=owner, skill_id=profile_skill.pk)
    assert ProfileSkill.objects.get(pk=second_profile_skill.pk).position == 0

    second_experience_skill = create_experience_skill(
        account=owner,
        experience_id=experience.pk,
        label="Elixir",
    )
    delete_experience_skill(account=owner, experience_skill_id=experience_skill.pk)
    assert ExperienceSkill.objects.get(pk=second_experience_skill.pk).position == 0

    second_project_skill = create_project_skill(
        account=owner,
        project_id=project.pk,
        label="Kotlin",
    )
    delete_project_skill(account=owner, project_skill_id=project_skill.pk)
    assert ProjectSkill.objects.get(pk=second_project_skill.pk).position == 0


@pytest.mark.django_db
def test_candidate_can_create_view_edit_reorder_and_remove_experience_entries() -> None:
    account = verified_account("candidate@example.com")
    create_profile(account)
    client = Client()
    client.force_login(account)

    first_response = client.post(reverse("experience_create"), experience_data())
    second_response = client.post(
        reverse("experience_create"),
        experience_data(role="Staff engineer", organization="Second Company"),
    )
    assert first_response.url == reverse("profile")
    assert second_response.url == reverse("profile")
    first, second = Experience.objects.order_by("position")
    assert [first.role, second.role] == ["Senior engineer", "Staff engineer"]

    edit_response = client.post(
        reverse("experience_edit", args=[first.pk]),
        experience_data(role="Principal engineer"),
    )
    move_response = client.post(
        reverse("experience_reorder", args=[second.pk]),
        {"direction": "up"},
    )
    assert edit_response.url == reverse("profile")
    assert move_response.url == reverse("profile")
    assert list(Experience.objects.values_list("role", flat=True)) == [
        "Staff engineer",
        "Principal engineer",
    ]

    delete_response = client.post(reverse("experience_delete", args=[first.pk]))
    assert delete_response.url == reverse("profile")
    assert list(Experience.objects.values_list("role", flat=True)) == ["Staff engineer"]
    assert Experience.objects.get().position == 0


@pytest.mark.django_db
def test_experience_current_role_and_date_validation_are_consistent() -> None:
    account = verified_account("candidate@example.com")
    create_profile(account)
    client = Client()
    client.force_login(account)

    invalid_response = client.post(
        reverse("experience_create"),
        experience_data(end_date="2019-12-31"),
    )
    assert invalid_response.status_code == 200
    assert b"End date must be on or after the start date" in invalid_response.content
    assert not Experience.objects.exists()

    missing_location_response = client.post(
        reverse("experience_create"),
        experience_data(location=""),
    )
    assert missing_location_response.status_code == 200
    assert b"This field is required." in missing_location_response.content
    assert not Experience.objects.exists()

    current_response = client.post(
        reverse("experience_create"),
        experience_data(end_date=""),
    )
    assert current_response.status_code == 302
    experience = Experience.objects.get()
    assert experience.end_date is None

    experience.end_date = experience.start_date.replace(year=2019)
    with pytest.raises(ValidationError):
        experience.full_clean()
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Experience.objects.create(
                profile=account.candidate_profile,
                role="Invalid role",
                organization="Invalid Company",
                start_date="2020-01-01",
                end_date="2019-12-31",
            )


@pytest.mark.django_db
def test_candidate_can_manage_ordered_highlights_inside_an_experience() -> None:
    account = verified_account("candidate@example.com")
    profile = create_profile(account)
    client = Client()
    client.force_login(account)
    experience = Experience.objects.create(
        profile=profile,
        role="Senior engineer",
        organization="Analytical Engines Ltd",
        start_date="2020-01-01",
    )

    first_response = client.post(
        reverse("highlight_create", args=[experience.pk]),
        {"text": "Reduced processing time by 40%."},
    )
    second_response = client.post(
        reverse("highlight_create", args=[experience.pk]),
        {"text": "Mentored three engineers."},
    )
    assert first_response.url == reverse("profile")
    assert second_response.url == reverse("profile")
    first, second = experience.highlights.order_by("position")

    move_response = client.post(
        reverse("highlight_reorder", args=[experience.pk, second.pk]),
        {"direction": "up"},
    )
    edit_response = client.post(
        reverse("highlight_edit", args=[experience.pk, first.pk]),
        {"text": "Reduced processing time by 50%."},
    )
    delete_response = client.post(reverse("highlight_delete", args=[experience.pk, first.pk]))
    assert move_response.url == reverse("profile")
    assert edit_response.url == reverse("profile")
    assert delete_response.url == reverse("profile")
    assert list(experience.highlights.values_list("text", flat=True)) == [
        "Mentored three engineers."
    ]
    assert experience.highlights.get().position == 0


@pytest.mark.django_db
def test_experience_htmx_validation_and_success_match_ordinary_persistence() -> None:
    account = verified_account("candidate@example.com")
    create_profile(account)
    client = Client()
    client.force_login(account)

    invalid_response = client.post(
        reverse("experience_create"),
        experience_data(end_date="2019-12-31"),
        headers={"HX-Request": "true"},
    )
    valid_response = client.post(
        reverse("experience_create"),
        experience_data(end_date=""),
        headers={"HX-Request": "true"},
    )

    assert invalid_response.status_code == 200
    assert b"End date must be on or after the start date" in invalid_response.content
    assert valid_response.status_code == 200
    assert valid_response.headers["HX-Redirect"] == reverse("profile")
    assert Experience.objects.filter(role="Senior engineer", end_date=None).exists()


@pytest.mark.django_db
def test_experience_and_highlight_operations_cannot_cross_account_boundaries() -> None:
    owner = verified_account("owner@example.com")
    intruder = verified_account("intruder@example.com")
    profile = create_profile(owner)
    create_profile(intruder)
    experience = Experience.objects.create(
        profile=profile,
        role="Senior engineer",
        organization="Analytical Engines Ltd",
        start_date="2020-01-01",
    )
    highlight = Highlight.objects.create(experience=experience, text="Private achievement.")
    client = Client()
    client.force_login(intruder)

    requests = [
        (reverse("experience_edit", args=[experience.pk]), {}, "get"),
        (reverse("experience_delete", args=[experience.pk]), {}, "post"),
        (reverse("experience_reorder", args=[experience.pk]), {}, "post"),
        (reverse("highlight_create", args=[experience.pk]), {}, "post"),
        (
            reverse("highlight_edit", args=[experience.pk, highlight.pk]),
            {"text": "Changed by intruder."},
            "post",
        ),
        (
            reverse("highlight_delete", args=[experience.pk, highlight.pk]),
            {},
            "post",
        ),
        (
            reverse("highlight_reorder", args=[experience.pk, highlight.pk]),
            {"direction": "up"},
            "post",
        ),
    ]
    for url, data, method in requests:
        response = getattr(client, method)(url, data)
        assert response.status_code == 404

    assert Experience.objects.get(pk=experience.pk).role == "Senior engineer"
    assert Highlight.objects.get(pk=highlight.pk).text == "Private achievement."


def education_data(**overrides: str) -> dict[str, str]:
    data = {
        "institution": "University of London",
        "degree": "BSc Mathematics",
        "start_date": "2015-09-01",
        "end_date": "2018-06-30",
    }
    data.update(overrides)
    return data


def project_data(**overrides: str) -> dict[str, str]:
    data = {
        "name": "Analytical Engine Visualizer",
        "description": "A visual exploration of mechanical computation.",
        "url": "https://example.com/analytical-engine",
    }
    data.update(overrides)
    return data


def skill_data(**overrides: str) -> dict[str, str]:
    data = {"label": "Python"}
    data.update(overrides)
    return data


def language_data(**overrides: str) -> dict[str, str]:
    data = {"name": "English", "proficiency": "fluent"}
    data.update(overrides)
    return data


@pytest.mark.django_db
def test_candidate_can_create_view_edit_reorder_and_remove_education_entries() -> None:
    account = verified_account("candidate@example.com")
    create_profile(account)
    client = Client()
    client.force_login(account)

    first_response = client.post(reverse("education_create"), education_data())
    second_response = client.post(
        reverse("education_create"),
        education_data(institution="University of Cambridge", degree="MPhil Computer Science"),
    )
    assert first_response.url == reverse("profile")
    assert second_response.url == reverse("profile")
    first, second = Education.objects.order_by("position")
    assert [first.institution, second.institution] == [
        "University of London",
        "University of Cambridge",
    ]

    edit_response = client.post(
        reverse("education_edit", args=[first.pk]),
        education_data(degree="BSc Applied Mathematics"),
    )
    move_response = client.post(
        reverse("education_reorder", args=[second.pk]),
        {"direction": "up"},
    )
    assert edit_response.url == reverse("profile")
    assert move_response.url == reverse("profile")
    assert list(Education.objects.values_list("institution", flat=True)) == [
        "University of Cambridge",
        "University of London",
    ]

    delete_response = client.post(reverse("education_delete", args=[first.pk]))
    assert delete_response.url == reverse("profile")
    assert list(Education.objects.values_list("institution", flat=True)) == [
        "University of Cambridge"
    ]
    assert Education.objects.get().position == 0


@pytest.mark.django_db
def test_education_current_dates_and_htmx_validation_are_consistent() -> None:
    account = verified_account("candidate@example.com")
    create_profile(account)
    client = Client()
    client.force_login(account)

    invalid_response = client.post(
        reverse("education_create"),
        education_data(end_date="2015-08-31"),
    )
    assert invalid_response.status_code == 200
    assert b"End date must be on or after the start date" in invalid_response.content
    assert not Education.objects.exists()

    current_response = client.post(
        reverse("education_create"),
        education_data(end_date=""),
        headers={"HX-Request": "true"},
    )
    assert current_response.status_code == 200
    assert current_response.headers["HX-Redirect"] == reverse("profile")
    assert Education.objects.get().end_date is None

    education = Education.objects.get()
    education.end_date = education.start_date.replace(year=2014)
    with pytest.raises(ValidationError):
        education.full_clean()
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Education.objects.create(
                profile=account.candidate_profile,
                institution="Invalid University",
                degree="Invalid degree",
                start_date="2018-01-01",
                end_date="2017-01-01",
            )


@pytest.mark.django_db
def test_candidate_can_create_view_edit_reorder_and_remove_project_entries() -> None:
    account = verified_account("candidate@example.com")
    create_profile(account)
    client = Client()
    client.force_login(account)

    first_response = client.post(reverse("project_create"), project_data())
    second_response = client.post(
        reverse("project_create"),
        project_data(name="Open source compiler"),
    )
    assert first_response.url == reverse("profile")
    assert second_response.url == reverse("profile")
    first, second = Project.objects.order_by("position")
    assert [first.name, second.name] == ["Analytical Engine Visualizer", "Open source compiler"]

    edit_response = client.post(
        reverse("project_edit", args=[first.pk]),
        project_data(description="A refreshed project description."),
    )
    move_response = client.post(
        reverse("project_reorder", args=[second.pk]),
        {"direction": "up"},
    )
    assert edit_response.url == reverse("profile")
    assert move_response.url == reverse("profile")
    assert list(Project.objects.values_list("name", flat=True)) == [
        "Open source compiler",
        "Analytical Engine Visualizer",
    ]

    delete_response = client.post(reverse("project_delete", args=[first.pk]))
    assert delete_response.url == reverse("profile")
    assert list(Project.objects.values_list("name", flat=True)) == ["Open source compiler"]
    assert Project.objects.get().position == 0


@pytest.mark.django_db
def test_project_htmx_validation_and_success_match_ordinary_persistence() -> None:
    account = verified_account("candidate@example.com")
    create_profile(account)
    client = Client()
    client.force_login(account)

    invalid_response = client.post(
        reverse("project_create"),
        project_data(name=""),
        headers={"HX-Request": "true"},
    )
    valid_response = client.post(
        reverse("project_create"),
        project_data(url=""),
        headers={"HX-Request": "true"},
    )

    assert invalid_response.status_code == 200
    assert b"This field is required." in invalid_response.content
    assert valid_response.status_code == 200
    assert valid_response.headers["HX-Redirect"] == reverse("profile")
    assert Project.objects.filter(name="Analytical Engine Visualizer", url="").exists()


@pytest.mark.django_db
def test_candidate_can_add_a_project_skill_through_the_shared_catalog() -> None:
    account = verified_account("project-skills@example.com")
    profile = create_profile(account)
    project = Project.objects.create(
        profile=profile,
        name="Legacy project",
    )
    concept = SkillConcept.objects.create(canonical_name="Node.js")
    SkillAlias.objects.create(concept=concept, display_name="nodejs")
    client = Client()
    client.force_login(account)

    response = client.post(
        reverse("project_skill_create", args=[project.pk]),
        {"label": " NodeJS "},
    )

    assert response.status_code == 302
    assert response.url == reverse("profile")
    project_skill = ProjectSkill.objects.get(project=project)
    assert project_skill.label == "NodeJS"
    assert project_skill.concept_id == concept.pk
    assert project_skill.position == 0
    profile_response = client.get(reverse("profile"))
    assert b"Legacy technologies" not in profile_response.content
    assert b"NodeJS" in profile_response.content


@pytest.mark.django_db
def test_project_skills_resolve_aliases_create_unknown_concepts_and_are_unique_per_project() -> (
    None
):
    account = verified_account("project-skill-catalog@example.com")
    profile = create_profile(account)
    first_project = Project.objects.create(profile=profile, name="First project")
    second_project = Project.objects.create(profile=profile, name="Second project")
    concept = SkillConcept.objects.create(canonical_name="Node.js")
    SkillAlias.objects.create(concept=concept, display_name="nodejs")
    client = Client()
    client.force_login(account)

    first_response = client.post(
        reverse("project_skill_create", args=[first_project.pk]),
        {"label": "NodeJS"},
    )
    duplicate_response = client.post(
        reverse("project_skill_create", args=[first_project.pk]),
        {"label": " node.js "},
    )
    second_response = client.post(
        reverse("project_skill_create", args=[second_project.pk]),
        {"label": "node.js"},
    )
    unknown_response = client.post(
        reverse("project_skill_create", args=[first_project.pk]),
        {"label": "  Elixir  "},
        headers={"HX-Request": "true"},
    )
    catalog_count_before_blank = SkillConcept.objects.count()
    blank_response = client.post(
        reverse("project_skill_create", args=[first_project.pk]),
        {"label": "  "},
        headers={"HX-Request": "true"},
    )

    assert first_response.url == reverse("profile")
    assert duplicate_response.status_code == 200
    assert b"already used in this project" in duplicate_response.content
    assert second_response.url == reverse("profile")
    assert unknown_response.headers["HX-Redirect"] == reverse("profile")
    assert blank_response.status_code == 200
    assert b"Enter a hard-skill label" in blank_response.content
    assert ProjectSkill.objects.filter(project=first_project, concept=concept).count() == 1
    assert ProjectSkill.objects.filter(project=second_project, concept=concept).count() == 1
    assert (
        ProjectSkill.objects.get(project=first_project, label="Elixir").concept.canonical_name
        == "Elixir"
    )
    assert SkillConcept.objects.count() == catalog_count_before_blank


@pytest.mark.django_db
def test_project_skills_can_reorder_delete_with_htmx_and_preserve_legacy_text_on_edit() -> None:
    account = verified_account("project-skill-management@example.com")
    profile = create_profile(account)
    project = Project.objects.create(
        profile=profile,
        name="Legacy project",
    )
    client = Client()
    client.force_login(account)

    client.post(reverse("project_skill_create", args=[project.pk]), {"label": "Python"})
    client.post(reverse("project_skill_create", args=[project.pk]), {"label": "Django"})
    first, second = ProjectSkill.objects.order_by("position")

    edit_response = client.post(
        reverse("project_edit", args=[project.pk]),
        project_data(name="Renamed project"),
        headers={"HX-Request": "true"},
    )
    reorder_response = client.post(
        reverse("project_skill_reorder", args=[second.pk]),
        {"direction": "up"},
    )
    htmx_reorder_response = client.post(
        reverse("project_skill_reorder", args=[second.pk]),
        {"direction": "down"},
        headers={"HX-Request": "true"},
    )
    project.refresh_from_db()

    assert edit_response.headers["HX-Redirect"] == reverse("profile")
    assert reorder_response.url == reverse("profile")
    assert htmx_reorder_response.headers["HX-Redirect"] == reverse("profile")
    assert list(project.project_skills.values_list("label", flat=True)) == ["Python", "Django"]

    delete_response = client.post(
        reverse("project_skill_delete", args=[first.pk]),
        headers={"HX-Request": "true"},
    )

    project.refresh_from_db()
    assert delete_response.headers["HX-Redirect"] == reverse("profile")
    assert list(project.project_skills.values_list("label", flat=True)) == ["Django"]
    assert project.project_skills.get().position == 0


@pytest.mark.django_db
def test_project_skill_mutations_cannot_cross_account_boundaries() -> None:
    owner = verified_account("project-skill-owner@example.com")
    intruder = verified_account("project-skill-intruder@example.com")
    owner_profile = create_profile(owner)
    create_profile(intruder)
    project = Project.objects.create(profile=owner_profile, name="Private project")
    concept, _ = resolve_skill_label("Python")
    project_skill = ProjectSkill.objects.create(
        project=project,
        concept=concept,
        label="Python",
        position=0,
    )
    client = Client()
    client.force_login(intruder)

    requests = [
        (reverse("project_skill_create", args=[project.pk]), {"label": "Django"}),
        (reverse("project_skill_delete", args=[project_skill.pk]), {}),
        (
            reverse("project_skill_reorder", args=[project_skill.pk]),
            {"direction": "up"},
        ),
    ]
    for url, data in requests:
        response = client.post(url, data)
        assert response.status_code == 404

    assert ProjectSkill.objects.get(pk=project_skill.pk).label == "Python"


@pytest.mark.django_db
def test_deleting_a_project_removes_private_project_skills_but_not_catalog_data() -> None:
    account = verified_account("project-skill-delete-project@example.com")
    profile = create_profile(account)
    project = Project.objects.create(profile=profile, name="Disposable project")
    concept, _ = resolve_skill_label("Python")
    ProjectSkill.objects.create(project=project, concept=concept, label="Python")
    client = Client()
    client.force_login(account)

    response = client.post(reverse("project_delete", args=[project.pk]))

    assert response.url == reverse("profile")
    assert not ProjectSkill.objects.filter(project=project).exists()
    assert SkillConcept.objects.filter(pk=concept.pk).exists()


@pytest.mark.django_db
def test_education_htmx_edit_reorder_and_delete_match_ordinary_persistence() -> None:
    account = verified_account("candidate@example.com")
    create_profile(account)
    client = Client()
    client.force_login(account)
    client.post(reverse("education_create"), education_data())
    client.post(
        reverse("education_create"),
        education_data(institution="University of Cambridge", degree="MPhil Computer Science"),
    )
    first, second = Education.objects.order_by("position")

    edit_response = client.post(
        reverse("education_edit", args=[first.pk]),
        education_data(degree="BSc Applied Mathematics"),
        headers={"HX-Request": "true"},
    )
    reorder_response = client.post(
        reverse("education_reorder", args=[second.pk]),
        {"direction": "up"},
        headers={"HX-Request": "true"},
    )
    delete_response = client.post(
        reverse("education_delete", args=[first.pk]),
        headers={"HX-Request": "true"},
    )

    assert edit_response.headers["HX-Redirect"] == reverse("profile")
    assert reorder_response.headers["HX-Redirect"] == reverse("profile")
    assert delete_response.headers["HX-Redirect"] == reverse("profile")
    assert list(Education.objects.values_list("institution", flat=True)) == [
        "University of Cambridge"
    ]


@pytest.mark.django_db
def test_project_htmx_edit_reorder_and_delete_match_ordinary_persistence() -> None:
    account = verified_account("candidate@example.com")
    create_profile(account)
    client = Client()
    client.force_login(account)
    client.post(reverse("project_create"), project_data())
    client.post(reverse("project_create"), project_data(name="Open source compiler"))
    first, second = Project.objects.order_by("position")

    edit_response = client.post(
        reverse("project_edit", args=[first.pk]),
        project_data(description="A refreshed project description."),
        headers={"HX-Request": "true"},
    )
    reorder_response = client.post(
        reverse("project_reorder", args=[second.pk]),
        {"direction": "up"},
        headers={"HX-Request": "true"},
    )
    delete_response = client.post(
        reverse("project_delete", args=[first.pk]),
        headers={"HX-Request": "true"},
    )

    assert edit_response.headers["HX-Redirect"] == reverse("profile")
    assert reorder_response.headers["HX-Redirect"] == reverse("profile")
    assert delete_response.headers["HX-Redirect"] == reverse("profile")
    assert list(Project.objects.values_list("name", flat=True)) == ["Open source compiler"]


@pytest.mark.django_db
def test_education_and_project_operations_cannot_cross_account_boundaries() -> None:
    owner = verified_account("owner@example.com")
    intruder = verified_account("intruder@example.com")
    profile = create_profile(owner)
    create_profile(intruder)
    education = Education.objects.create(
        profile=profile,
        institution="University of London",
        degree="BSc Mathematics",
        start_date="2015-09-01",
    )
    project = Project.objects.create(
        profile=profile,
        name="Private project",
        description="Private description.",
    )
    client = Client()
    client.force_login(intruder)

    requests = [
        (reverse("education_edit", args=[education.pk]), education_data(), "post"),
        (reverse("education_delete", args=[education.pk]), {}, "post"),
        (reverse("education_reorder", args=[education.pk]), {}, "post"),
        (reverse("project_edit", args=[project.pk]), project_data(), "post"),
        (reverse("project_delete", args=[project.pk]), {}, "post"),
        (reverse("project_reorder", args=[project.pk]), {}, "post"),
    ]
    for url, data, method in requests:
        response = getattr(client, method)(url, data)
        assert response.status_code == 404

    assert Education.objects.get(pk=education.pk).institution == "University of London"
    assert Project.objects.get(pk=project.pk).description == "Private description."


@pytest.mark.django_db
def test_candidate_can_manage_ordered_unique_skills() -> None:
    account = verified_account("candidate@example.com")
    create_profile(account)
    client = Client()
    client.force_login(account)

    first_response = client.post(reverse("skill_create"), skill_data())
    second_response = client.post(
        reverse("skill_create"),
        skill_data(label="Django"),
        headers={"HX-Request": "true"},
    )
    third_response = client.post(reverse("skill_create"), skill_data(label="Rust"))
    duplicate_response = client.post(reverse("skill_create"), skill_data(label=" python "))
    whitespace_response = client.post(reverse("skill_create"), skill_data(label="   "))

    assert first_response.url == reverse("profile")
    assert second_response.headers["HX-Redirect"] == reverse("profile")
    assert third_response.url == reverse("profile")
    assert duplicate_response.status_code == 200
    assert whitespace_response.status_code == 200
    assert b"Enter a hard-skill label." in whitespace_response.content
    assert b"already" in duplicate_response.content
    assert list(ProfileSkill.objects.values_list("label", flat=True)) == [
        "Python",
        "Django",
        "Rust",
    ]

    first, second, _third = ProfileSkill.objects.order_by("position")
    full_page_reorder_response = client.post(
        reverse("skill_reorder", args=[second.pk]),
        {"direction": "up"},
    )
    htmx_reorder_response = client.post(
        reverse("skill_reorder", args=[second.pk]),
        {"direction": "down"},
        headers={"HX-Request": "true"},
    )
    full_page_delete_response = client.post(reverse("skill_delete", args=[first.pk]))
    htmx_delete_response = client.post(
        reverse("skill_delete", args=[second.pk]),
        headers={"HX-Request": "true"},
    )

    assert full_page_reorder_response.url == reverse("profile")
    assert htmx_reorder_response.headers["HX-Redirect"] == reverse("profile")
    assert full_page_delete_response.url == reverse("profile")
    assert htmx_delete_response.headers["HX-Redirect"] == reverse("profile")
    assert list(ProfileSkill.objects.values_list("label", flat=True)) == ["Rust"]
    assert ProfileSkill.objects.get().position == 0


@pytest.mark.django_db
def test_experience_skills_resolve_aliases_preserve_labels_and_stay_independent() -> None:
    account = verified_account("experience-skill-catalog@example.com")
    profile = create_profile(account)
    first_experience = Experience.objects.create(
        profile=profile,
        role="Senior engineer",
        organization="First Company",
        start_date="2020-01-01",
    )
    second_experience = Experience.objects.create(
        profile=profile,
        role="Staff engineer",
        organization="Second Company",
        start_date="2023-01-01",
    )
    concept = SkillConcept.objects.create(canonical_name="Node.js")
    SkillAlias.objects.create(concept=concept, display_name="nodejs")
    client = Client()
    client.force_login(account)

    first_response = client.post(
        reverse("experience_skill_create", args=[first_experience.pk]),
        {"label": "NodeJS"},
    )
    second_response = client.post(
        reverse("experience_skill_create", args=[second_experience.pk]),
        {"label": "Elixir"},
        headers={"HX-Request": "true"},
    )

    assert first_response.url == reverse("profile")
    assert second_response.headers["HX-Redirect"] == reverse("profile")
    assert list(ExperienceSkill.objects.values_list("experience_id", "label", "concept_id")) == [
        (first_experience.pk, "NodeJS", concept.pk),
        (second_experience.pk, "Elixir", SkillConcept.objects.get(canonical_key="elixir").pk),
    ]
    assert not ProfileSkill.objects.filter(profile=profile).exists()


@pytest.mark.django_db
def test_experience_skills_support_duplicate_feedback_blank_validation_and_ordered_management() -> (
    None
):
    account = verified_account("experience-skill-management@example.com")
    profile = create_profile(account)
    experience = Experience.objects.create(
        profile=profile,
        role="Senior engineer",
        organization="First Company",
        start_date="2020-01-01",
    )
    python, _ = resolve_skill_label("Python")
    SkillAlias.objects.create(concept=python, display_name="py")
    client = Client()
    client.force_login(account)

    first_response = client.post(
        reverse("experience_skill_create", args=[experience.pk]),
        {"label": "Python"},
    )
    second_response = client.post(
        reverse("experience_skill_create", args=[experience.pk]),
        {"label": "Django"},
    )
    duplicate_response = client.post(
        reverse("experience_skill_create", args=[experience.pk]),
        {"label": "py"},
        headers={"HX-Request": "true"},
    )
    catalog_count_before_blank = SkillConcept.objects.count()
    blank_response = client.post(
        reverse("experience_skill_create", args=[experience.pk]),
        {"label": "  "},
        headers={"HX-Request": "true"},
    )
    profile_response_before_reorder = client.get(reverse("profile"))
    first, second = ExperienceSkill.objects.order_by("position")
    reorder_response = client.post(
        reverse("experience_skill_reorder", args=[second.pk]),
        {"direction": "up"},
    )
    htmx_reorder_response = client.post(
        reverse("experience_skill_reorder", args=[second.pk]),
        {"direction": "down"},
        headers={"HX-Request": "true"},
    )
    delete_response = client.post(
        reverse("experience_skill_delete", args=[first.pk]),
        headers={"HX-Request": "true"},
    )
    profile_response = client.get(reverse("profile"))

    assert first_response.url == reverse("profile")
    assert second_response.url == reverse("profile")
    assert duplicate_response.status_code == 200
    assert b"already used in this experience" in duplicate_response.content
    assert blank_response.status_code == 200
    assert b"Enter a hard-skill label" in blank_response.content
    assert SkillConcept.objects.count() == catalog_count_before_blank
    rendered_skills = profile_response_before_reorder.content
    assert rendered_skills.index(b">Python</span>") < rendered_skills.index(b">Django</span>")
    assert reorder_response.url == reverse("profile")
    assert htmx_reorder_response.headers["HX-Redirect"] == reverse("profile")
    assert delete_response.headers["HX-Redirect"] == reverse("profile")
    assert profile_response.status_code == 200
    assert b"Experience skills" in profile_response.content
    assert list(experience.experience_skills.values_list("label", flat=True)) == ["Django"]
    assert experience.experience_skills.get().position == 0


@pytest.mark.django_db
def test_experience_skills_can_repeat_a_concept_across_experiences_and_profile() -> None:
    account = verified_account("experience-skill-independence@example.com")
    profile = create_profile(account)
    first_experience = Experience.objects.create(
        profile=profile,
        role="Senior engineer",
        organization="First Company",
        start_date="2020-01-01",
    )
    second_experience = Experience.objects.create(
        profile=profile,
        role="Staff engineer",
        organization="Second Company",
        start_date="2023-01-01",
    )
    client = Client()
    client.force_login(account)

    client.post(
        reverse("experience_skill_create", args=[first_experience.pk]),
        {"label": "Python"},
    )
    client.post(
        reverse("experience_skill_create", args=[second_experience.pk]),
        {"label": "python"},
    )
    client.post(reverse("skill_create"), {"label": "PYTHON"})

    concept = SkillConcept.objects.get(canonical_key="python")
    profile_response = client.get(reverse("profile"))
    assert ExperienceSkill.objects.filter(concept=concept).count() == 2
    assert ProfileSkill.objects.get(profile=profile).concept_id == concept.pk
    assert list(first_experience.experience_skills.values_list("label", flat=True)) == ["Python"]
    assert list(second_experience.experience_skills.values_list("label", flat=True)) == ["python"]
    assert profile_response.content.count(b">Python</span>") == 1
    assert profile_response.content.count(b">python</span>") == 1
    assert profile_response.content.count(b">PYTHON</span>") == 1


@pytest.mark.django_db
def test_experience_skill_mutations_cannot_cross_account_boundaries() -> None:
    owner = verified_account("experience-skill-owner@example.com")
    intruder = verified_account("experience-skill-intruder@example.com")
    owner_profile = create_profile(owner)
    create_profile(intruder)
    experience = Experience.objects.create(
        profile=owner_profile,
        role="Senior engineer",
        organization="Private Company",
        start_date="2020-01-01",
    )
    concept, _ = resolve_skill_label("Python")
    experience_skill = ExperienceSkill.objects.create(
        experience=experience,
        concept=concept,
        label="Python",
        position=0,
    )
    client = Client()
    client.force_login(intruder)

    requests = [
        (reverse("experience_skill_create", args=[experience.pk]), {"label": "Django"}),
        (reverse("experience_skill_delete", args=[experience_skill.pk]), {}),
        (
            reverse("experience_skill_reorder", args=[experience_skill.pk]),
            {"direction": "up"},
        ),
    ]
    for url, data in requests:
        response = client.post(url, data)
        assert response.status_code == 404

    assert ExperienceSkill.objects.get(pk=experience_skill.pk).label == "Python"


@pytest.mark.django_db
def test_deleting_an_experience_or_account_removes_private_experience_skills_only() -> None:
    account = verified_account("experience-skill-delete-experience@example.com")
    profile = create_profile(account)
    experience = Experience.objects.create(
        profile=profile,
        role="Disposable role",
        organization="Private Company",
        start_date="2020-01-01",
    )
    concept, _ = resolve_skill_label("Python")
    alias = SkillAlias.objects.create(concept=concept, display_name="py")
    ExperienceSkill.objects.create(experience=experience, concept=concept, label="Python")
    client = Client()
    client.force_login(account)

    response = client.post(reverse("experience_delete", args=[experience.pk]))

    assert response.url == reverse("profile")
    assert not ExperienceSkill.objects.filter(experience=experience).exists()
    assert SkillConcept.objects.filter(pk=concept.pk).exists()

    account_to_delete = verified_account("experience-skill-delete-account@example.com")
    second_profile = create_profile(account_to_delete)
    second_experience = Experience.objects.create(
        profile=second_profile,
        role="Account role",
        organization="Private Company",
        start_date="2020-01-01",
    )
    ExperienceSkill.objects.create(experience=second_experience, concept=concept, label="Python")

    deletion_client = Client()
    deletion_client.force_login(account_to_delete)
    deletion_response = deletion_client.post(reverse("account_delete"), {"confirm": "1"})

    assert deletion_response.url == reverse("home")
    assert not ExperienceSkill.objects.filter(experience=second_experience).exists()
    assert SkillConcept.objects.filter(pk=concept.pk).exists()
    assert SkillAlias.objects.filter(pk=alias.pk, concept=concept).exists()


@pytest.mark.django_db
def test_profile_skills_resolve_catalog_aliases_and_preserve_entered_labels() -> None:
    account = verified_account("profile-skill-catalog@example.com")
    profile = create_profile(account)
    concept = SkillConcept.objects.create(canonical_name="Node.js")
    SkillAlias.objects.create(concept=concept, display_name="nodejs")
    client = Client()
    client.force_login(account)

    first_response = client.post(reverse("skill_create"), skill_data(label="NodeJS"))
    duplicate_response = client.post(
        reverse("skill_create"),
        skill_data(label="node.js"),
        headers={"HX-Request": "true"},
    )
    unknown_response = client.post(
        reverse("skill_create"),
        skill_data(label="  Elixir  "),
    )

    assert first_response.url == reverse("profile")
    assert duplicate_response.status_code == 200
    assert b"already in your profile" in duplicate_response.content
    assert unknown_response.url == reverse("profile")
    assert list(
        ProfileSkill.objects.filter(profile=profile).values_list("label", "concept__canonical_name")
    ) == [("NodeJS", "Node.js"), ("Elixir", "Elixir")]


@pytest.mark.django_db
def test_skill_association_schema_has_label_constraints_without_normalized_columns() -> None:
    account = verified_account("skill-schema@example.com")
    profile = create_profile(account)
    experience = Experience.objects.create(
        profile=profile,
        role="Engineer",
        organization="Example",
        start_date="2020-01-01",
    )
    project = Project.objects.create(profile=profile, name="Example project")
    concept, _ = resolve_skill_label("Python")
    models_and_constraints = (
        (ProfileSkill, profile, "profile_skill_label_not_blank"),
        (ExperienceSkill, experience, "experience_skill_label_not_blank"),
        (ProjectSkill, project, "project_skill_label_not_blank"),
    )

    with connection.cursor() as cursor:
        for model, _owner, constraint_name in models_and_constraints:
            columns = {
                column.name
                for column in connection.introspection.get_table_description(
                    cursor, model._meta.db_table
                )
            }
            assert "normalized_label" not in columns
            assert constraint_name in {constraint.name for constraint in model._meta.constraints}

    with pytest.raises(IntegrityError):
        model, owner, _ = models_and_constraints[0]
        with transaction.atomic():
            model.objects.bulk_create([model(profile=owner, concept=concept, label="", position=0)])
    with pytest.raises(IntegrityError):
        model, owner, _ = models_and_constraints[1]
        with transaction.atomic():
            model.objects.bulk_create(
                [model(experience=owner, concept=concept, label="", position=0)]
            )
    with pytest.raises(IntegrityError):
        model, owner, _ = models_and_constraints[2]
        with transaction.atomic():
            model.objects.bulk_create([model(project=owner, concept=concept, label="", position=0)])


@pytest.mark.django_db
def test_candidate_can_manage_languages_with_proficiency_and_htmx_feedback() -> None:
    account = verified_account("candidate@example.com")
    create_profile(account)
    client = Client()
    client.force_login(account)

    invalid_response = client.post(
        reverse("language_create"),
        language_data(name="", proficiency="unknown"),
        headers={"HX-Request": "true"},
    )
    assert invalid_response.status_code == 200
    assert b"This field is required." in invalid_response.content
    assert b"Select a valid choice" in invalid_response.content
    assert not Language.objects.exists()

    first_response = client.post(reverse("language_create"), language_data())
    second_response = client.post(
        reverse("language_create"),
        language_data(name="French", proficiency="intermediate"),
    )
    first, second = Language.objects.order_by("position")
    edit_response = client.post(
        reverse("language_edit", args=[first.pk]),
        language_data(proficiency="native"),
        headers={"HX-Request": "true"},
    )
    assert Language.objects.get(pk=first.pk).proficiency == "native"
    reorder_response = client.post(
        reverse("language_reorder", args=[second.pk]),
        {"direction": "up"},
        headers={"HX-Request": "true"},
    )
    delete_response = client.post(
        reverse("language_delete", args=[first.pk]),
        headers={"HX-Request": "true"},
    )

    assert first_response.url == reverse("profile")
    assert second_response.url == reverse("profile")
    assert edit_response.headers["HX-Redirect"] == reverse("profile")
    assert reorder_response.headers["HX-Redirect"] == reverse("profile")
    assert delete_response.headers["HX-Redirect"] == reverse("profile")
    assert list(Language.objects.values_list("name", "proficiency")) == [("French", "intermediate")]


@pytest.mark.django_db
def test_skill_and_language_operations_cannot_cross_account_boundaries() -> None:
    owner = verified_account("owner@example.com")
    intruder = verified_account("intruder@example.com")
    profile = create_profile(owner)
    create_profile(intruder)
    concept, _ = resolve_skill_label("Python")
    skill = ProfileSkill.objects.create(
        profile=profile,
        concept=concept,
        label="Python",
        position=0,
    )
    language = Language.objects.create(
        profile=profile,
        name="English",
        proficiency="fluent",
        position=0,
    )
    client = Client()
    client.force_login(intruder)

    requests = [
        (reverse("skill_delete", args=[skill.pk]), {}, "post"),
        (reverse("skill_reorder", args=[skill.pk]), {"direction": "up"}, "post"),
        (reverse("language_edit", args=[language.pk]), language_data(), "post"),
        (reverse("language_delete", args=[language.pk]), {}, "post"),
        (reverse("language_reorder", args=[language.pk]), {"direction": "up"}, "post"),
    ]
    for url, data, method in requests:
        response = getattr(client, method)(url, data)
        assert response.status_code == 404

    assert ProfileSkill.objects.get(pk=skill.pk).label == "Python"
    assert Language.objects.get(pk=language.pk).name == "English"
