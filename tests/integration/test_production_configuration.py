import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_production_settings(**overrides: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {
            "DJANGO_SETTINGS_MODULE": "config.settings.production",
            "DJANGO_SECRET_KEY": (
                "a-production-secret-that-is-long-enough-for-django-security-checks-123"
            ),
            "DATABASE_URL": "postgresql://user:password@db.example.com:5432/applykit",
            "DJANGO_ALLOWED_HOSTS": "app.example.com",
            "RESEND_API_KEY": "re_test_key",
            "DEFAULT_FROM_EMAIL": "ApplyKit <noreply@example.com>",
            **overrides,
        }
    )
    return subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import django; django.setup(); "
                "from django.conf import settings; print(settings.EMAIL_BACKEND)"
            ),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_production_settings_use_resend_with_postgresql() -> None:
    result = load_production_settings()

    assert result.returncode == 0, result.stderr
    assert "anymail.backends.resend.EmailBackend" in result.stdout


def test_production_settings_reject_sqlite_database() -> None:
    result = load_production_settings(DATABASE_URL="sqlite:///production.sqlite3")

    assert result.returncode != 0
    assert "PostgreSQL" in result.stderr


def test_production_settings_report_missing_resend_configuration() -> None:
    result = load_production_settings(RESEND_API_KEY="")

    assert result.returncode != 0
    assert "RESEND_API_KEY" in result.stderr


def test_production_settings_reject_short_secret() -> None:
    result = load_production_settings(DJANGO_SECRET_KEY="short-production-secret")

    assert result.returncode != 0
    assert "at least 50 characters" in result.stderr
