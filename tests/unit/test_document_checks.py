from types import SimpleNamespace

from django.core.checks import Error, Warning

from apps.documents import checks


def _settings(*, require_linux_isolation: bool) -> SimpleNamespace:
    return SimpleNamespace(require_linux_isolation=require_linux_isolation)


def test_reduced_guarantees_are_reported_off_linux(monkeypatch):
    monkeypatch.setattr(checks.sys, "platform", "darwin")
    monkeypatch.setattr(
        checks, "current_settings", lambda: _settings(require_linux_isolation=False)
    )

    results = checks.check_document_isolation()

    assert len(results) == 1
    assert isinstance(results[0], Warning)
    assert results[0].id == "documents.W001"


def test_missing_linux_isolation_is_an_error_when_required(monkeypatch):
    monkeypatch.setattr(checks.sys, "platform", "darwin")
    monkeypatch.setattr(checks, "current_settings", lambda: _settings(require_linux_isolation=True))

    results = checks.check_document_isolation()

    assert len(results) == 1
    assert isinstance(results[0], Error)
    assert results[0].id == "documents.E001"


def test_linux_reports_no_isolation_issues(monkeypatch):
    monkeypatch.setattr(checks.sys, "platform", "linux")
    monkeypatch.setattr(checks, "current_settings", lambda: _settings(require_linux_isolation=True))

    assert checks.check_document_isolation() == []
