from datetime import UTC, datetime

import pytest

from apps.campaigns.progress import campaign_periods

pytestmark = pytest.mark.unit


def test_campaign_periods_use_monday_and_calendar_month_in_campaign_timezone() -> None:
    periods = campaign_periods(
        datetime(2026, 3, 1, 0, 30, tzinfo=UTC),
        "America/Los_Angeles",
    )

    assert periods.week_start == datetime(2026, 2, 23, 0, 0, tzinfo=periods.week_start.tzinfo)
    assert periods.week_end == datetime(2026, 3, 2, 0, 0, tzinfo=periods.week_end.tzinfo)
    assert periods.month_start == datetime(2026, 2, 1, 0, 0, tzinfo=periods.month_start.tzinfo)
    assert periods.month_end == datetime(2026, 3, 1, 0, 0, tzinfo=periods.month_end.tzinfo)
