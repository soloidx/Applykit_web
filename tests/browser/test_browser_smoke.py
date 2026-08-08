import os
import re

import pytest
from django.core import mail

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

    assert page.get_by_role("heading", name="Your private workspace").is_visible()
    assert page.get_by_text("browser@example.com").is_visible()
    assert page.get_by_text("No active campaign yet").is_visible()

    page.get_by_role("button", name="Sign out").click()
    assert page.get_by_role(
        "heading", name="Turn a scattered search into a focused campaign."
    ).is_visible()
