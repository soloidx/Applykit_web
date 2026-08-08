import pytest
from allauth.account.models import EmailAddress
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Account
from apps.profiles.models import (
    CandidateProfile,
    Education,
    Experience,
    Highlight,
    Language,
    PresentationPreferences,
    Project,
    Skill,
)

pytestmark = pytest.mark.integration


def verified_account(email: str) -> Account:
    account = Account.objects.create_user(email, "a-secure-password")
    EmailAddress.objects.create(user=account, email=account.email, primary=True, verified=True)
    return account


def profile_data(**overrides: str) -> dict[str, str]:
    data = {
        "full_name": "Ada Lovelace",
        "timezone": "Europe/London",
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
        "technologies": "Python, Django",
        "url": "https://example.com/analytical-engine",
    }
    data.update(overrides)
    return data


def skill_data(**overrides: str) -> dict[str, str]:
    data = {"name": "Python"}
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
        project_data(name="Open source compiler", technologies="Rust"),
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
        skill_data(name="Django"),
        headers={"HX-Request": "true"},
    )
    duplicate_response = client.post(reverse("skill_create"), skill_data(name=" python "))
    whitespace_response = client.post(reverse("skill_create"), skill_data(name="   "))

    assert first_response.url == reverse("profile")
    assert second_response.headers["HX-Redirect"] == reverse("profile")
    assert duplicate_response.status_code == 200
    assert whitespace_response.status_code == 200
    assert b"Enter a skill." in whitespace_response.content
    assert b"already" in duplicate_response.content
    assert list(Skill.objects.values_list("name", flat=True)) == ["Python", "Django"]

    first, second = Skill.objects.order_by("position")
    reorder_response = client.post(
        reverse("skill_reorder", args=[second.pk]),
        {"direction": "up"},
    )
    delete_response = client.post(reverse("skill_delete", args=[first.pk]))

    assert reorder_response.url == reverse("profile")
    assert delete_response.url == reverse("profile")
    assert list(Skill.objects.values_list("name", flat=True)) == ["Django"]
    assert Skill.objects.get().position == 0


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
def test_presentation_preferences_are_independent_from_career_history() -> None:
    account = verified_account("candidate@example.com")
    profile = create_profile(account)
    experience = Experience.objects.create(
        profile=profile,
        role="Senior engineer",
        organization="Analytical Engines Ltd",
        location="London, UK",
        start_date="2020-01-01",
    )
    client = Client()
    client.force_login(account)

    response = client.post(
        reverse("presentation_preferences"),
        {
            "show_contact_details": "",
            "show_professional_summary": "",
            "show_experience": "on",
            "show_education": "",
            "show_projects": "on",
            "show_skills": "on",
            "show_languages": "",
        },
    )

    assert response.url == reverse("profile")
    preferences = PresentationPreferences.objects.get(profile=profile)
    assert preferences.show_contact_details is False
    assert preferences.show_experience is True
    assert preferences.show_languages is False
    assert Experience.objects.get(pk=experience.pk).role == "Senior engineer"

    htmx_response = client.post(
        reverse("presentation_preferences"),
        {
            "show_contact_details": "on",
            "show_professional_summary": "on",
            "show_experience": "on",
            "show_education": "on",
            "show_projects": "",
            "show_skills": "on",
            "show_languages": "on",
        },
        headers={"HX-Request": "true"},
    )

    assert htmx_response.headers["HX-Redirect"] == reverse("profile")
    assert PresentationPreferences.objects.get(profile=profile).show_projects is False


@pytest.mark.django_db
def test_skill_language_and_preference_operations_cannot_cross_account_boundaries() -> None:
    owner = verified_account("owner@example.com")
    intruder = verified_account("intruder@example.com")
    profile = create_profile(owner)
    create_profile(intruder)
    skill = Skill.objects.create(profile=profile, name="Python", position=0)
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

    assert Skill.objects.get(pk=skill.pk).name == "Python"
    assert Language.objects.get(pk=language.pk).name == "English"
    preferences_response = client.post(
        reverse("presentation_preferences"),
        {"show_experience": "on"},
    )

    assert preferences_response.status_code == 302
    assert PresentationPreferences.objects.filter(
        profile=intruder.candidate_profile,
        show_experience=True,
    ).exists()
    assert not PresentationPreferences.objects.filter(profile=profile).exists()
