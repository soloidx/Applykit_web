import os
import re

import pytest
from allauth.account.models import EmailAddress
from django.core import mail
from django.urls import reverse

from apps.accounts.models import Account
from apps.applications.models import Company, JobApplication, RecruitmentEvent
from apps.campaigns.models import Campaign
from apps.profiles.models import CandidateProfile

# pytest-playwright starts its sync API from an async-managed fixture context.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.browser


def test_browser_suite_is_configured(page, live_server) -> None:
    page.goto(live_server.url)

    heading = page.get_by_role("heading", name="Turn a scattered search into a focused campaign.")
    assert heading.is_visible()


@pytest.mark.django_db
def test_verified_candidate_can_sign_in_and_sign_out_in_browser(page, live_server) -> None:
    page.goto(f"{live_server.url}/accounts/signup/")
    page.get_by_label("Email").fill("browser@example.com")
    page.locator('input[name="password1"]').fill("a-secure-password")
    page.locator('input[name="password2"]').fill("a-secure-password")
    page.get_by_role("button", name="Create workspace").click()

    confirmation_link = re.search(r"https?://\S+", mail.outbox[0].body).group(0)
    page.goto(confirmation_link)
    page.get_by_role("button", name="Confirm").click()

    page.goto(f"{live_server.url}/accounts/login/")
    page.get_by_label("Email").fill("browser@example.com")
    page.get_by_label("Password").fill("a-secure-password")
    page.get_by_role("button", name="Sign in").click()

    assert page.get_by_role("heading", name="Start with the essentials.").is_visible()
    page.get_by_label("Full name").fill("Browser Candidate")
    page.get_by_label("Timezone").fill("America/New_York")
    page.get_by_role("button", name="Save profile").click()
    page.wait_for_url("**/dashboard/")

    assert page.get_by_role("heading", name="Your private workspace").is_visible()
    assert page.get_by_text("Browser Candidate").is_visible()
    assert page.get_by_text("No active campaign yet").is_visible()

    page.get_by_role("button", name="Sign out").click()
    assert page.get_by_role(
        "heading", name="Turn a scattered search into a focused campaign."
    ).is_visible()


@pytest.mark.django_db
def test_candidate_can_use_mobile_navigation_and_read_only_applications_board(
    page, live_server
) -> None:
    account = Account.objects.create_user("board-browser@example.com", "a-secure-password")
    EmailAddress.objects.create(
        user=account,
        email=account.email,
        primary=True,
        verified=True,
    )
    CandidateProfile.objects.create(
        account=account,
        full_name="Board Candidate",
        timezone="America/New_York",
    )
    Campaign.objects.create(
        account=account,
        weekly_target=5,
        monthly_target=20,
        timezone="America/New_York",
    )
    company = Company.objects.create(name="Example Careers")
    application = JobApplication.objects.create(
        account=account,
        campaign=Campaign.objects.get(account=account),
        company=company,
        role_title="Platform engineer",
        job_description="Build dependable internal systems.",
    )

    page.set_viewport_size({"width": 375, "height": 800})
    page.goto(f"{live_server.url}/accounts/login/")
    page.get_by_label("Email").fill("board-browser@example.com")
    page.get_by_label("Password").fill("a-secure-password")
    page.get_by_role("button", name="Sign in").click()
    page.get_by_text("Menu", exact=True).click()
    page.get_by_role("link", name="Applications").click()

    board = page.locator('[aria-label="Applications by stage"]')
    assert page.get_by_role("heading", name="Draft").is_visible()
    assert page.get_by_role("heading", name="Submitted").is_visible()
    assert page.get_by_role("heading", name="Interviewing").is_visible()
    assert page.get_by_role("heading", name="Offer").is_visible()
    assert page.get_by_role("heading", name="Accepted").is_visible()
    assert page.get_by_role("heading", name="Rejected").is_visible()
    assert page.get_by_role("heading", name="Withdrawn").is_visible()
    assert board.evaluate("element => element.scrollWidth > element.clientWidth")
    page.get_by_role("link", name="Platform engineer").click()
    page.wait_for_url(f"**{reverse('application_detail', args=[application.pk])}")


@pytest.mark.django_db
def test_candidate_can_update_stage_and_see_history_and_progress_in_browser(
    page, live_server
) -> None:
    account = Account.objects.create_user("stage-browser@example.com", "a-secure-password")
    EmailAddress.objects.create(
        user=account,
        email=account.email,
        primary=True,
        verified=True,
    )
    CandidateProfile.objects.create(
        account=account,
        full_name="Browser Candidate",
        timezone="America/New_York",
    )
    Campaign.objects.create(
        account=account,
        weekly_target=5,
        monthly_target=20,
        timezone="America/New_York",
    )
    company = Company.objects.create(name="Example Careers")
    application = JobApplication.objects.create(
        account=account,
        campaign=Campaign.objects.get(account=account),
        company=company,
        role_title="Platform engineer",
        job_description="Build dependable internal systems.",
    )

    page.goto(f"{live_server.url}/accounts/login/")
    page.get_by_label("Email").fill("stage-browser@example.com")
    page.get_by_label("Password").fill("a-secure-password")
    page.get_by_role("button", name="Sign in").click()
    page.goto(f"{live_server.url}{reverse('application_detail', args=[application.pk])}")
    page.get_by_label("Stage").select_option(JobApplication.Stage.SUBMITTED)
    page.get_by_role("button", name="Update stage").click()

    assert page.get_by_text("Submitted", exact=True).first.is_visible()
    assert page.get_by_text("Draft to Submitted").is_visible()
    page.get_by_role("link", name="Back to workspace").click()
    page.wait_for_url(f"**{reverse('dashboard')}")

    assert page.get_by_text("1 / 5").is_visible()
    assert page.get_by_text("1 / 20").is_visible()


@pytest.mark.django_db
def test_candidate_can_cancel_and_confirm_application_deletion_with_progress_warning_in_browser(
    page, live_server
) -> None:
    account = Account.objects.create_user("delete-browser@example.com", "a-secure-password")
    EmailAddress.objects.create(
        user=account,
        email=account.email,
        primary=True,
        verified=True,
    )
    CandidateProfile.objects.create(
        account=account,
        full_name="Browser Candidate",
        timezone="America/New_York",
    )
    campaign = Campaign.objects.create(
        account=account,
        weekly_target=5,
        monthly_target=20,
        timezone="America/New_York",
    )
    company = Company.objects.create(name="Example Careers")
    application = JobApplication.objects.create(
        account=account,
        campaign=campaign,
        company=company,
        role_title="Platform engineer",
        job_description="Build dependable internal systems.",
    )

    page.goto(f"{live_server.url}/accounts/login/")
    page.get_by_label("Email").fill("delete-browser@example.com")
    page.get_by_label("Password").fill("a-secure-password")
    page.get_by_role("button", name="Sign in").click()
    page.goto(f"{live_server.url}{reverse('application_detail', args=[application.pk])}")
    page.get_by_label("Stage").select_option(JobApplication.Stage.SUBMITTED)
    page.get_by_role("button", name="Update stage").click()
    page.get_by_role("link", name="Delete application").click()

    assert page.get_by_text("Permanent deletion").is_visible()
    assert (
        page.get_by_role("alert")
        .get_by_text("This application contributes to Campaign Progress.")
        .is_visible()
    )
    page.get_by_role("button", name="Cancel").click()
    page.wait_for_url(f"**{reverse('application_detail', args=[application.pk])}")
    assert page.get_by_role("heading", name="Platform engineer").is_visible()

    page.get_by_role("link", name="Delete application").click()
    page.get_by_role("button", name="Delete permanently").click()
    page.wait_for_url(f"**{reverse('dashboard')}")

    assert page.get_by_text("0 / 5").is_visible()
    assert page.get_by_text("0 / 20").is_visible()


@pytest.mark.django_db
def test_candidate_can_schedule_complete_and_follow_a_recruitment_event_in_browser(
    page, live_server
) -> None:
    account = Account.objects.create_user("event-browser@example.com", "a-secure-password")
    EmailAddress.objects.create(
        user=account,
        email=account.email,
        primary=True,
        verified=True,
    )
    CandidateProfile.objects.create(
        account=account,
        full_name="Browser Candidate",
        timezone="America/New_York",
    )
    Campaign.objects.create(
        account=account,
        weekly_target=5,
        monthly_target=20,
        timezone="America/New_York",
    )
    company = Company.objects.create(name="Example Careers")
    application = JobApplication.objects.create(
        account=account,
        campaign=Campaign.objects.get(account=account),
        company=company,
        role_title="Platform engineer",
        job_description="Build dependable internal systems.",
    )

    page.goto(f"{live_server.url}/accounts/login/")
    page.get_by_label("Email").fill("event-browser@example.com")
    page.get_by_label("Password").fill("a-secure-password")
    page.get_by_role("button", name="Sign in").click()
    page.goto(f"{live_server.url}{reverse('application_detail', args=[application.pk])}")
    page.get_by_label("Event type").select_option(RecruitmentEvent.EventType.INTERVIEW)
    page.get_by_label("Scheduled time (America/New_York)").fill("2030-05-01T10:00")
    page.get_by_role("button", name="Schedule event").click()
    page.wait_for_url(f"**{reverse('application_detail', args=[application.pk])}")
    page.wait_for_load_state("load")

    page.get_by_role("link", name="Back to workspace").click()
    page.wait_for_url(f"**{reverse('dashboard')}")
    assert page.get_by_role("heading", name="Upcoming recruitment events").is_visible()
    assert page.get_by_text("May 1, 2030, 10:00 America/New_York").is_visible()
    page.get_by_text("Interview", exact=True).click()
    page.get_by_label("Status").select_option(RecruitmentEvent.Status.COMPLETED)
    page.get_by_role("button", name="Save event").click()
    page.wait_for_url(f"**{reverse('application_detail', args=[application.pk])}")
    page.wait_for_load_state("load")
    page.reload()

    assert page.get_by_text("Completed · May 1, 2030, 10:00 America/New_York").is_visible()
    page.get_by_role("link", name="Back to workspace").click()
    page.wait_for_url(f"**{reverse('dashboard')}")
    assert not page.get_by_text("Interview", exact=True).is_visible()


@pytest.mark.django_db
def test_candidate_can_cancel_and_confirm_account_deletion_in_browser(page, live_server) -> None:
    account = Account.objects.create_user("account-delete-browser@example.com", "a-secure-password")
    EmailAddress.objects.create(
        user=account,
        email=account.email,
        primary=True,
        verified=True,
    )
    CandidateProfile.objects.create(
        account=account,
        full_name="Browser Candidate",
        timezone="America/New_York",
    )

    page.goto(f"{live_server.url}/accounts/login/")
    page.get_by_label("Email").fill("account-delete-browser@example.com")
    page.get_by_label("Password").fill("a-secure-password")
    page.get_by_role("button", name="Sign in").click()
    page.get_by_role("link", name="Edit your profile").click()
    page.get_by_role("link", name="Delete your account").click()

    assert page.get_by_role("heading", name="Delete your ApplyKit account?").is_visible()
    assert page.get_by_role("alert").get_by_text("Shared public Companies").is_visible()
    page.get_by_role("button", name="Cancel").click()
    page.wait_for_url(f"**{reverse('profile')}")
    assert page.get_by_role("heading", name="Leave ApplyKit").is_visible()

    page.get_by_role("link", name="Delete your account").click()
    page.get_by_role("button", name="Delete account permanently").click()
    page.wait_for_url(f"**{reverse('home')}")

    assert page.get_by_role(
        "heading", name="Turn a scattered search into a focused campaign."
    ).is_visible()
    assert page.get_by_role("link", name="Sign in").is_visible()
