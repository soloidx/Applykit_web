import pytest

pytestmark = pytest.mark.unit


def test_unit_suite_is_configured() -> None:
    assert "unit" in "tests/unit"
