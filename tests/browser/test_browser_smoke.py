import os

import pytest

# pytest-playwright starts its sync API from an async-managed fixture context.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.browser


def test_browser_suite_is_configured(page, live_server) -> None:
    page.goto(live_server.url)

    heading = page.get_by_role("heading", name="Turn a scattered search into a focused campaign.")
    assert heading.is_visible()
