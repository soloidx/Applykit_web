import os
import re

import pytest
from allauth.account.models import EmailAddress
from django.core import mail
from django.urls import reverse

from apps.accounts.models import Account
from apps.applications.models import (
    ApplicationSkillRequirement,
    Company,
    JobApplication,
    RecruitmentEvent,
)
from apps.campaigns.models import Campaign
from apps.cover_letters.models import CoverLetter
from apps.profiles.models import (
    CandidateProfile,
    Education,
    Experience,
    ExperienceSkill,
    Language,
    Project,
)
from apps.skills.models import SkillConcept

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
    page.get_by_label("Timezone").select_option("America/New_York")
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


def _document_candidate(email: str) -> tuple[Account, JobApplication]:
    account = Account.objects.create_user(email, "a-secure-password")
    EmailAddress.objects.create(
        user=account,
        email=account.email,
        primary=True,
        verified=True,
    )
    CandidateProfile.objects.create(
        account=account,
        full_name="Ada Lovelace",
        timezone="Europe/London",
    )
    campaign = Campaign.objects.create(
        account=account,
        weekly_target=5,
        monthly_target=20,
        timezone="Europe/London",
    )
    application = JobApplication.objects.create(
        account=account,
        campaign=campaign,
        company=Company.objects.create(name="Example Careers"),
        role_title="Platform engineer",
        job_description="Build dependable internal systems.",
    )
    return account, application


@pytest.mark.django_db
def test_document_workbenches_revert_semantically_and_protect_dirty_navigation(
    page, live_server
) -> None:
    account, application = _document_candidate("document-dirty-browser@example.com")

    page.goto(f"{live_server.url}/accounts/login/")
    page.get_by_label("Email").fill(account.email)
    page.get_by_label("Password").fill("a-secure-password")
    page.get_by_role("button", name="Sign in").click()
    page.goto(f"{live_server.url}{reverse('resume_detail', args=[application.pk])}")

    workbench = page.locator("[data-document-workbench]")
    assert workbench.get_attribute("data-save-state") == "clean"
    page.get_by_role("link", name="Reset Resume").click()
    reset_dialog = page.get_by_role("dialog")
    assert reset_dialog.get_by_text(
        "Your saved Resume is unchanged until you choose Save Resume."
    ).is_visible()
    assert page.evaluate("document.activeElement.textContent") == "Cancel"
    reset_dialog.get_by_role("button", name="Cancel").click()

    page.get_by_label("Full name").fill("Tailored Ada")
    assert page.get_by_text("Unsaved changes", exact=True).first.is_visible()
    page.locator('[name="header-full_name_inherit"]').check()
    assert page.get_by_text("Saved", exact=True).first.is_visible()

    page.get_by_label("Full name").fill("Tailored Ada")
    assert page.get_by_text("Unsaved changes", exact=True).first.is_visible()
    unload = page.evaluate(
        """
        () => {
          const event = new Event('beforeunload', { cancelable: true });
          window.dispatchEvent(event);
          return { prevented: event.defaultPrevented, returnValue: event.returnValue };
        }
        """
    )
    assert unload["prevented"] is True

    back = page.get_by_role("link", name="Back to application")
    back.click()
    dialog = page.get_by_role("dialog")
    assert dialog.get_by_role("heading", name="Discard unsaved changes?").is_visible()
    assert page.evaluate("document.activeElement.textContent") == "Keep editing"
    page.keyboard.press("Escape")
    assert not dialog.is_visible()
    assert page.evaluate("document.activeElement.textContent") == "Back to application"

    back.click()
    dialog.get_by_role("button", name="Discard changes").click()
    page.wait_for_url(f"**{reverse('application_detail', args=[application.pk])}")


@pytest.mark.django_db
def test_cover_letter_failed_save_preserves_draft_and_suppresses_duplicate_submit(
    page, live_server, monkeypatch: pytest.MonkeyPatch
) -> None:
    account, application = _document_candidate("cover-letter-failed-browser@example.com")
    CoverLetter.objects.create(application=application, body_html="<p>Saved body.</p>")

    def fail_save(self: CoverLetter, *args: object, **kwargs: object) -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(CoverLetter, "save", fail_save)
    page.goto(f"{live_server.url}/accounts/login/")
    page.get_by_label("Email").fill(account.email)
    page.get_by_label("Password").fill("a-secure-password")
    page.get_by_role("button", name="Sign in").click()
    page.goto(f"{live_server.url}{reverse('cover_letter_detail', args=[application.pk])}")

    editor = page.locator(".ql-editor")
    editor.fill("Retry me after a failed save.")
    form = page.locator("#cover-letter-form")
    form.evaluate(
        """
        form => {
          form.addEventListener('submit', event => event.preventDefault(), { once: true });
          form.requestSubmit();
        }
        """
    )
    page.wait_for_timeout(50)
    assert page.get_by_text("Saving...", exact=True).is_visible()
    assert editor.get_attribute("contenteditable") == "false"
    assert page.get_by_role("button", name="Save letter").first.is_disabled()
    page.reload()

    editor = page.locator(".ql-editor")
    editor.fill("Retry me after a failed save.")
    form = page.locator("#cover-letter-form")
    duplicate_prevented = form.evaluate(
        """
        form => {
          form.dataset.saving = 'true';
          const event = new SubmitEvent('submit', { cancelable: true });
          form.dispatchEvent(event);
          return event.defaultPrevented;
        }
        """,
    )
    assert duplicate_prevented is True
    save_button = page.get_by_role("button", name="Save letter").first
    form.evaluate("form => form.dataset.saving = 'false'")
    save_button.click()
    page.wait_for_load_state("load")

    assert page.get_by_text("Save failed", exact=True).is_visible()
    assert page.locator(".ql-editor").inner_text() == "Retry me after a failed save."
    assert save_button.is_enabled()
    assert page.get_by_role("button", name="Save letter").first.is_enabled()


@pytest.mark.django_db
def test_dirty_cover_letter_delete_names_saved_and_unsaved_content(page, live_server) -> None:
    account, application = _document_candidate("cover-letter-delete-browser@example.com")
    CoverLetter.objects.create(application=application, body_html="<p>Saved body.</p>")

    page.goto(f"{live_server.url}/accounts/login/")
    page.get_by_label("Email").fill(account.email)
    page.get_by_label("Password").fill("a-secure-password")
    page.get_by_role("button", name="Sign in").click()
    page.goto(f"{live_server.url}{reverse('cover_letter_detail', args=[application.pk])}")
    page.locator(".ql-editor").fill("Unsaved body.")
    page.get_by_role("button", name="Delete letter").click()

    dialog = page.get_by_role("dialog")
    assert dialog.get_by_text(
        "This permanently deletes the saved Cover Letter and discards your current unsaved draft."
    ).is_visible()
    assert page.evaluate("document.activeElement.textContent") == "Cancel"
    dialog.get_by_role("button", name="Cancel").click()
    assert not dialog.is_visible()

    lifecycle = page.locator("[data-document-workbench]")
    assert (
        lifecycle.evaluate(
            """
        root => {
          document.dispatchEvent(new CustomEvent('htmx:beforeCleanupElement', {
            detail: { elt: root },
          }));
          document.dispatchEvent(new CustomEvent('htmx:afterSwap', { detail: { target: root } }));
          return Boolean(root._documentWorkbench) && root.querySelectorAll('.ql-toolbar').length;
        }
        """
        )
        == 1
    )


@pytest.mark.django_db
def test_cover_letter_quill_initializes_once_across_htmx_replacement(page, live_server) -> None:
    account, application = _document_candidate("cover-letter-htmx-browser@example.com")
    CoverLetter.objects.create(application=application, body_html="<p>Saved letter body.</p>")
    _open_cover_letter(page, account, application, live_server.url)
    page.locator(".ql-editor").first.wait_for()

    state = page.evaluate(
        """
        () => {
          const root = document.querySelector('[data-document-workbench]');
          const replacement = root.cloneNode(true);
          const quillBefore = root._documentWorkbenchQuill;
          document.dispatchEvent(new CustomEvent('htmx:beforeCleanupElement', {
            detail: { elt: root },
          }));
          root.replaceWith(replacement);
          document.dispatchEvent(new CustomEvent('htmx:afterSwap', {
            detail: { target: replacement },
          }));
          const quillAfter = replacement._documentWorkbenchQuill;
          quillAfter.clipboard.dangerouslyPasteHTML('<p>Draft after replacement.</p>');
          return {
            instanceSurvived: quillBefore === quillAfter,
            initializedOnce: Boolean(quillAfter),
            editorCount: document.querySelectorAll('.ql-editor').length,
            toolbarCount: document.querySelectorAll('.ql-toolbar').length,
            textareaSyncs: replacement.querySelector('[data-cover-letter-input]').value,
          };
        }
        """
    )

    assert state["instanceSurvived"] is False
    assert state["initializedOnce"] is True
    assert state["editorCount"] == 1
    assert state["toolbarCount"] == 1
    assert "Draft after replacement." in state["textareaSyncs"]


@pytest.mark.django_db
def test_cover_letter_quill_toolbar_is_limited_to_paragraph_bold_italic_lists_and_link(
    page, live_server
) -> None:
    account, application = _document_candidate("cover-letter-toolbar-browser@example.com")
    CoverLetter.objects.create(application=application, body_html="<p>Saved letter body.</p>")
    _open_cover_letter(page, account, application, live_server.url)

    toolbar = page.locator("[data-toolbar]")
    paragraph = toolbar.locator("select")
    assert paragraph.locator("option").count() == 1
    assert paragraph.locator("option").inner_text() == "Paragraph"
    assert toolbar.get_by_role("button", name="Bold").is_visible()
    assert toolbar.get_by_role("button", name="Italic").is_visible()
    assert toolbar.get_by_role("button", name="Bulleted list").is_visible()
    assert toolbar.get_by_role("button", name="Numbered list").is_visible()
    assert toolbar.get_by_role("button", name="Add link").is_visible()


@pytest.mark.django_db
def test_cover_letter_quill_link_authoring_saves_and_renders_canonical_html(
    page, live_server
) -> None:
    account, application = _document_candidate("cover-letter-link-browser@example.com")
    CoverLetter.objects.create(application=application, body_html="<p>Saved letter body.</p>")
    _open_cover_letter(page, account, application, live_server.url)

    editor = page.locator(".ql-editor")
    editor.fill("Please consider my credentials.")
    page.evaluate(
        """
        () => {
          const quill = document.querySelector('[data-document-workbench]')._documentWorkbenchQuill;
          quill.setSelection(0, quill.getLength());
        }
        """
    )
    page.locator("[data-toolbar]").get_by_role("button", name="Add link").click()
    tooltip = page.locator(".ql-tooltip")
    tooltip.wait_for()
    tooltip.locator("input[data-link]").fill("https://example.com/link")
    tooltip.locator("a.ql-action").click()

    textarea = page.locator("[data-cover-letter-input]")
    assert "https://example.com/link" in textarea.input_value()

    query = page.get_by_role("button", name="Save letter").first
    query.click()
    page.wait_for_url(f"**{reverse('cover_letter_detail', args=[application.pk])}")
    page.wait_for_load_state("load")

    assert page.get_by_text("Saved", exact=True).first.is_visible()
    stored = CoverLetter.objects.get(application=application).body_html
    assert 'href="https://example.com/link"' in stored
    assert 'rel="nofollow noopener noreferrer"' in stored
    assert "target=" not in stored

    page.reload()
    link = page.locator(".ql-editor a")
    link.first.wait_for()
    assert link.first.inner_text() == "Please consider my credentials."
    assert link.first.get_attribute("href") == "https://example.com/link"

    editor.fill("This paragraph should never become unsafe.")
    page.evaluate(
        """
        () => {
          const quill = document.querySelector('[data-document-workbench]')._documentWorkbenchQuill;
          quill.setSelection(0, quill.getLength());
        }
        """
    )
    page.locator("[data-toolbar]").get_by_role("button", name="Add link").click()
    tooltip = page.locator(".ql-tooltip")
    tooltip.wait_for()
    tooltip.locator("input[data-link]").fill("javascript:alert(1)")
    tooltip.locator("a.ql-action").click()
    page.get_by_role("button", name="Save letter").first.click()
    page.wait_for_url(f"**{reverse('cover_letter_detail', args=[application.pk])}")
    page.wait_for_load_state("load")

    stored = CoverLetter.objects.get(application=application).body_html
    assert "javascript:" not in stored
    assert "about:blank" not in stored
    assert "This paragraph should never become unsafe." in stored

    page.reload()
    assert "javascript:" not in page.locator(".ql-editor").inner_html()


@pytest.mark.django_db
def test_cover_letter_workbench_stacks_context_above_editor_on_mobile(page, live_server) -> None:
    account, application = _document_candidate("cover-letter-layout-browser@example.com")
    CoverLetter.objects.create(application=application, body_html="<p>Saved letter body.</p>")
    _open_cover_letter(page, account, application, live_server.url)

    _assert_rail_beside_canvas_then_stacked(
        page,
        page.locator("main > aside"),
        page.locator("main > section"),
    )


@pytest.mark.django_db
def test_cover_letter_confirmed_deletion_returns_to_not_created_in_browser(
    page, live_server
) -> None:
    account, application = _document_candidate("cover-letter-delete-flow@example.com")
    CoverLetter.objects.create(
        application=application, body_html="<p>Delete me in the browser.</p>"
    )
    _open_cover_letter(page, account, application, live_server.url)

    page.get_by_role("button", name="Delete letter").click()
    dialog = page.get_by_role("dialog")
    assert dialog.get_by_role("heading", name="Delete this Cover Letter?").is_visible()
    dialog.get_by_role("button", name="Delete permanently").click()
    page.wait_for_url(f"**{reverse('cover_letter_detail', args=[application.pk])}")

    assert page.get_by_role("heading", name="Not created").is_visible()
    assert not CoverLetter.objects.filter(application=application).exists()


@pytest.mark.django_db
def test_cover_letter_starter_template_and_blank_drafts_stay_local(page, live_server) -> None:
    account, application = _document_candidate("cover-letter-template-flow@example.com")

    _sign_in(page, account, account.email, live_server.url)
    page.goto(
        f"{live_server.url}{reverse('cover_letter_detail', args=[application.pk])}?draft=template"
    )
    page.wait_for_load_state("load")

    editor = page.locator(".ql-editor")
    editor.first.wait_for()
    assert "Dear Hiring Team," in editor.inner_text()
    assert page.get_by_text("Unsaved changes", exact=True).first.is_visible()

    back = page.get_by_role("link", name="Back to application")
    back.click()
    dialog = page.get_by_role("dialog")
    assert dialog.get_by_role("heading", name="Discard unsaved changes?").is_visible()
    dialog.get_by_role("button", name="Keep editing").click()
    assert not dialog.is_visible()
    assert not CoverLetter.objects.filter(application=application).exists()

    page.goto(
        f"{live_server.url}{reverse('cover_letter_detail', args=[application.pk])}?draft=blank"
    )
    page.wait_for_load_state("load")

    editor = page.locator(".ql-editor")
    assert editor.inner_text().strip() == ""
    assert page.get_by_text("Saved", exact=True).first.is_visible()
    back.click()
    page.wait_for_url(f"**{reverse('application_detail', args=[application.pk])}")
    assert not CoverLetter.objects.filter(application=application).exists()


@pytest.mark.django_db
def test_cover_letter_textarea_save_works_with_javascript_disabled(live_server, browser) -> None:
    account, application = _document_candidate("cover-letter-nojs@example.com")
    CoverLetter.objects.create(application=application, body_html="<p>Saved letter body.</p>")

    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        page.goto(f"{live_server.url}/accounts/login/")
        page.get_by_label("Email").fill(account.email)
        page.get_by_label("Password").fill("a-secure-password")
        page.get_by_role("button", name="Sign in").click()
        page.goto(f"{live_server.url}{reverse('cover_letter_detail', args=[application.pk])}")

        textarea = page.locator("[data-cover-letter-input]")
        assert textarea.is_visible()
        textarea.fill("Authored without JavaScript.")
        page.get_by_role("button", name="Save letter").first.click()
        page.wait_for_url(f"**{reverse('cover_letter_detail', args=[application.pk])}")
        page.wait_for_load_state("load")

        assert page.get_by_text("Saved", exact=True).first.is_visible()
        assert (
            "Authored without JavaScript."
            in CoverLetter.objects.get(application=application).body_html
        )
    finally:
        context.close()


def _open_cover_letter(page, account: Account, application: JobApplication, url: str) -> None:
    _sign_in(page, account, account.email, url)
    page.goto(f"{url}{reverse('cover_letter_detail', args=[application.pk])}")
    page.wait_for_load_state("load")


def _assert_rail_beside_canvas_then_stacked(page, rail, canvas) -> None:
    page.set_viewport_size({"width": 1280, "height": 900})
    rail_box = rail.bounding_box()
    canvas_box = canvas.bounding_box()
    assert rail_box is not None and canvas_box is not None
    assert rail_box["x"] + rail_box["width"] <= canvas_box["x"]
    assert rail_box["y"] < canvas_box["y"] + canvas_box["height"]

    page.set_viewport_size({"width": 375, "height": 800})
    page.wait_for_timeout(200)
    rail_box = rail.bounding_box()
    canvas_box = canvas.bounding_box()
    assert rail_box is not None and canvas_box is not None
    assert rail_box["x"] == canvas_box["x"]
    assert rail_box["y"] + rail_box["height"] <= canvas_box["y"] + 2


def _resume_workbench_candidate(email: str) -> tuple[Account, JobApplication]:
    account = Account.objects.create_user(email, "a-secure-password")
    EmailAddress.objects.create(
        user=account,
        email=account.email,
        primary=True,
        verified=True,
    )
    profile = CandidateProfile.objects.create(
        account=account,
        full_name="Ada Lovelace",
        professional_summary="A dependable engineer.",
        timezone="Europe/London",
    )
    campaign = Campaign.objects.create(
        account=account,
        weekly_target=5,
        monthly_target=20,
        timezone="Europe/London",
    )
    application = JobApplication.objects.create(
        account=account,
        campaign=campaign,
        company=Company.objects.create(name="Example Careers"),
        role_title="Platform engineer",
        job_description="Build dependable Python systems.",
    )
    concept = SkillConcept.objects.create(canonical_name="Python")
    first = Experience.objects.create(
        profile=profile,
        role="Backend engineer",
        organization="FirstOrg",
        location="London",
        start_date="2020-01-01",
        description="Built internal systems.",
    )
    ExperienceSkill.objects.create(experience=first, concept=concept, label="Python")
    Experience.objects.create(
        profile=profile,
        role="Intern",
        organization="SecondOrg",
        location="London",
        start_date="2021-01-01",
    )
    Education.objects.create(
        profile=profile,
        institution="University",
        degree="Mathematics",
        start_date="2016-01-01",
    )
    Project.objects.create(profile=profile, name="Toolkit", description="A toolkit.")
    Language.objects.create(
        profile=profile,
        name="English",
        proficiency=Language.Proficiency.NATIVE,
    )
    ApplicationSkillRequirement.objects.create(
        application=application,
        concept=concept,
        label="Python",
        classification=ApplicationSkillRequirement.Classification.REQUIRED,
    )
    return account, application


def _sign_in(page, account: Account, email: str, live_server_url: str) -> None:
    page.goto(f"{live_server_url}/accounts/login/")
    page.get_by_label("Email").fill(email)
    page.get_by_label("Password").fill("a-secure-password")
    page.get_by_role("button", name="Sign in").click()


@pytest.mark.django_db
def test_resume_workbench_source_rail_beside_canvas_on_desktop_and_stacked_on_mobile(
    page, live_server
) -> None:
    account, application = _resume_workbench_candidate("resume-layout-browser@example.com")
    _sign_in(page, account, account.email, live_server.url)
    page.goto(f"{live_server.url}{reverse('resume_detail', args=[application.pk])}")
    page.wait_for_load_state("load")

    _assert_rail_beside_canvas_then_stacked(
        page,
        page.locator("aside"),
        page.locator("#resume-form"),
    )


@pytest.mark.django_db
def test_resume_workbench_source_rail_toggles_include_and_hide(page, live_server) -> None:
    account, application = _resume_workbench_candidate("resume-rail-toggle-browser@example.com")
    _sign_in(page, account, account.email, live_server.url)
    page.goto(f"{live_server.url}{reverse('resume_detail', args=[application.pk])}")
    page.wait_for_load_state("load")

    rail_experience = page.locator('[data-source-entry][data-formset="experiences"]').first
    assert rail_experience.locator("[data-source-status]").inner_text() == "Included"
    assert (
        rail_experience.locator("[data-action=source-toggle]")
        .inner_text()
        .strip()
        .startswith("Hide from Resume")
    )
    assert page.get_by_text("Saved", exact=True).first.is_visible()

    rail_experience.locator("[data-action=source-toggle]").click()

    assert rail_experience.locator("[data-source-status]").inner_text() == "Available to add"
    assert rail_experience.locator("button").inner_text().strip().startswith("Add back to Resume")
    assert page.get_by_text("Unsaved changes", exact=True).first.is_visible()

    first_card = page.locator('#resume-form [data-formset="experiences"]').first
    assert first_card.locator('[name$="-included"]').is_checked() is False

    rail_experience.locator("[data-action=source-toggle]").click()
    assert rail_experience.locator("[data-source-status]").inner_text() == "Included"
    assert rail_experience.locator("button").inner_text().strip().startswith("Hide from Resume")
    assert first_card.locator('[name$="-included"]').is_checked() is True


@pytest.mark.django_db
def test_resume_workbench_move_controls_reorder_sections_without_drag_and_drop(
    page, live_server
) -> None:
    account, application = _resume_workbench_candidate("resume-move-browser@example.com")
    _sign_in(page, account, account.email, live_server.url)
    page.goto(f"{live_server.url}{reverse('resume_detail', args=[application.pk])}")
    page.wait_for_load_state("load")

    rows = page.locator("[data-section-row]")
    assert rows.count() >= 2
    first_position = rows.nth(0).locator('[name$="-position"]').element_handle()
    second_position = rows.nth(1).locator('[name$="-position"]').element_handle()
    assert first_position.input_value() == "0"
    assert second_position.input_value() == "1"

    rows.nth(0).locator('[data-action="move-down"]').click()

    assert first_position.input_value() == "1"
    assert second_position.input_value() == "0"
    assert page.get_by_text("Unsaved changes", exact=True).first.is_visible()


@pytest.mark.django_db
def test_resume_workbench_move_controls_reorder_items_without_drag_and_drop(
    page, live_server
) -> None:
    account, application = _resume_workbench_candidate("resume-move-items-browser@example.com")
    _sign_in(page, account, account.email, live_server.url)
    page.goto(f"{live_server.url}{reverse('resume_detail', args=[application.pk])}")
    page.wait_for_load_state("load")

    cards = page.locator('#resume-form [data-formset="experiences"]')
    assert cards.count() == 2
    first_position = cards.nth(0).locator('[name$="-position"]').element_handle()
    second_position = cards.nth(1).locator('[name$="-position"]').element_handle()
    assert first_position.input_value() == "0"
    assert second_position.input_value() == "1"

    cards.nth(0).locator('[data-action="move-down"]').click()

    assert first_position.input_value() == "1"
    assert second_position.input_value() == "0"
    assert page.get_by_text("Unsaved changes", exact=True).first.is_visible()


@pytest.mark.django_db
def test_resume_workbench_save_reload_preserves_moves_hides_and_tailoring(
    page, live_server
) -> None:
    account, application = _resume_workbench_candidate("resume-save-reload-browser@example.com")
    experiences_sources = list(
        Experience.objects.filter(profile=account.candidate_profile).order_by("position", "id")
    )
    first_experience_id = str(experiences_sources[0].pk)
    second_experience_id = str(experiences_sources[1].pk)
    _sign_in(page, account, account.email, live_server.url)
    page.goto(f"{live_server.url}{reverse('resume_detail', args=[application.pk])}")
    page.wait_for_load_state("load")

    page.get_by_label("Full name").fill("Tailored Ada")
    page.locator("[data-section-row]").nth(0).locator('[data-action="move-down"]').click()
    cards = page.locator('#resume-form [data-formset="experiences"]')
    cards.nth(1).locator('[data-action="move-up"]').click()
    page.locator("aside details", has_text="Projects").locator("summary").click()
    rail_project = page.locator('[data-source-entry][data-formset="projects"]').first
    rail_project.locator("[data-action=source-toggle]").click()

    page.get_by_role("button", name="Save Resume").first.click()
    page.wait_for_url(f"**{reverse('resume_detail', args=[application.pk])}")
    page.wait_for_load_state("load")

    assert page.get_by_text("Saved", exact=True).first.is_visible()
    assert page.get_by_label("Full name").input_value() == "Tailored Ada"

    sections = page.locator("[data-section-row]")
    assert sections.nth(0).get_attribute("data-section-kind") == "skills"
    assert sections.nth(1).get_attribute("data-section-kind") == "summary"

    experiences = page.locator('#resume-form [data-formset="experiences"]')
    assert experiences.nth(0).get_attribute("data-source-id") == second_experience_id
    assert experiences.nth(1).get_attribute("data-source-id") == first_experience_id

    page.locator("aside details", has_text="Projects").locator("summary").click()
    project_entry = page.locator('[data-source-entry][data-formset="projects"]').first
    assert project_entry.locator("[data-source-status]").inner_text() == "Available to add"

    page.reload()
    assert page.get_by_label("Full name").input_value() == "Tailored Ada"
    assert page.get_by_text("Saved", exact=True).first.is_visible()
    assert page.locator("[data-section-row]").nth(0).get_attribute("data-section-kind") == "skills"


@pytest.mark.django_db
def test_resume_workbench_core_editing_works_with_javascript_disabled(live_server, browser) -> None:
    account, application = _resume_workbench_candidate("resume-nojs-browser@example.com")
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        page.goto(f"{live_server.url}/accounts/login/")
        page.get_by_label("Email").fill(account.email)
        page.get_by_label("Password").fill("a-secure-password")
        page.get_by_role("button", name="Sign in").click()
        page.goto(f"{live_server.url}{reverse('resume_detail', args=[application.pk])}")

        page.get_by_label("Full name").fill("No-JS Tailored")
        page.locator('[name="header-full_name_inherit"]').uncheck()
        sections = page.locator("[data-section-row]")
        sections.nth(0).locator('[name$="-position"]').fill("1")
        sections.nth(1).locator('[name$="-position"]').fill("0")
        page.locator('#resume-form [data-formset="projects"]').first.locator(
            '[name$="-included"]'
        ).uncheck()

        page.get_by_role("button", name="Save Resume").first.click()
        page.wait_for_url(f"**{reverse('resume_detail', args=[application.pk])}")
        page.wait_for_load_state("load")

        assert page.get_by_text("Saved", exact=True).first.is_visible()
        assert page.get_by_label("Full name").input_value() == "No-JS Tailored"
        sections = page.locator("[data-section-row]")
        assert sections.nth(0).get_attribute("data-section-kind") == "skills"
        assert sections.nth(1).get_attribute("data-section-kind") == "summary"
        project_checkbox = page.locator('#resume-form [data-formset="projects"]').first.locator(
            '[name$="-included"]'
        )
        assert project_checkbox.is_checked() is False
    finally:
        context.close()
