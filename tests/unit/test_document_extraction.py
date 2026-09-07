import io
import logging
import os
from pathlib import Path

import pytest

from apps.documents import extraction, storage, telemetry
from apps.documents.conf import ExtractionSettings
from apps.documents.extraction import DocumentExtractionError, extract_docx


def make_config(tmp_path: Path, **overrides: object) -> ExtractionSettings:
    values = {
        "max_upload_bytes": 10 * 1024 * 1024,
        "max_members": 500,
        "max_expanded_bytes": 64 * 1024 * 1024,
        "max_member_bytes": 32 * 1024 * 1024,
        "max_code_points": 100_000,
        "cpu_seconds": 10,
        "wall_seconds": 15.0,
        "memory_bytes": 512 * 1024 * 1024,
        "temp_root": tmp_path / "private",
        "require_linux_isolation": False,
    }
    values.update(overrides)
    return ExtractionSettings(**values)  # type: ignore[arg-type]


def test_extracts_bounded_canonical_text(make_docx, tmp_path):
    source = make_docx(headings=["Profile"], paragraphs=["Cafe\u0301 body text."])
    result = extract_docx(io.BytesIO(source.read_bytes()), config=make_config(tmp_path))
    assert "Caf\u00e9 body text." in result.text
    assert "# Profile" in result.text
    assert result.code_points == len(result.text)


def test_rejects_upload_over_size_budget(make_docx, tmp_path):
    source = make_docx(paragraphs=["Hello."])
    config = make_config(tmp_path, max_upload_bytes=10)
    with pytest.raises(DocumentExtractionError) as raised:
        extract_docx(io.BytesIO(source.read_bytes()), config=config)
    assert raised.value.category == "over_budget"


def test_rejects_unsupported_package(tmp_path):
    config = make_config(tmp_path)
    with pytest.raises(DocumentExtractionError) as raised:
        extract_docx(io.BytesIO(b"\xd0\xcf\x11\xe0" + b"\x00" * 64), config=config)
    assert raised.value.category == "unsupported_format"


def test_rejects_malformed_package(tmp_path):
    config = make_config(tmp_path)
    with pytest.raises(DocumentExtractionError) as raised:
        extract_docx(io.BytesIO(b"definitely not a docx at all"), config=config)
    assert raised.value.category == "malformed_document"


def test_rejects_text_over_code_point_budget(make_docx, tmp_path):
    source = make_docx(paragraphs=["word " * 60])
    config = make_config(tmp_path, max_code_points=100)
    with pytest.raises(DocumentExtractionError) as raised:
        extract_docx(io.BytesIO(source.read_bytes()), config=config)
    assert raised.value.category == "over_budget"


def test_removes_private_storage_on_success(make_docx, tmp_path):
    source = make_docx(paragraphs=["Hello."])
    config = make_config(tmp_path)
    extract_docx(io.BytesIO(source.read_bytes()), config=config)
    assert list((tmp_path / "private").iterdir()) == []


def test_cleanup_failure_fails_safely_marks_unhealthy_and_emits_security_event(
    make_docx, tmp_path, caplog, monkeypatch
):
    source = make_docx(paragraphs=["Hello."])
    config = make_config(tmp_path)

    def failing_remove(path: Path) -> None:
        raise OSError("simulated removal failure")

    monkeypatch.setattr(storage, "remove_private_dir", failing_remove)
    with caplog.at_level(logging.WARNING, logger=telemetry.SECURITY_LOGGER_NAME):
        with pytest.raises(DocumentExtractionError) as raised:
            extract_docx(io.BytesIO(source.read_bytes()), config=config)
    assert raised.value.category == "internal_error"
    assert telemetry.is_instance_healthy() is False
    assert any(record.event == "document_extraction.cleanup_failed" for record in caplog.records)
    telemetry._unhealthy.clear()


def test_production_fails_closed_without_linux_isolation(tmp_path, monkeypatch):
    config = make_config(tmp_path, require_linux_isolation=True)
    monkeypatch.setattr(extraction, "_is_linux", lambda: False)
    with pytest.raises(DocumentExtractionError) as raised:
        extract_docx(io.BytesIO(b"unused"), config=config)
    assert raised.value.category == "extraction_unavailable"


def test_telemetry_never_contains_source_content(make_docx, tmp_path, caplog):
    source = make_docx(paragraphs=["SecretMarkerBodyText"])
    secret_bytes = source.read_bytes()
    with caplog.at_level(logging.DEBUG):
        result = extract_docx(io.BytesIO(secret_bytes), config=make_config(tmp_path))
    assert "SecretMarkerBodyText" in result.text
    for record in caplog.records:
        assert "SecretMarkerBodyText" not in record.getMessage()
        assert not any("SecretMarkerBodyText" in str(value) for value in record.__dict__.values())


def test_temp_root_is_the_only_written_location(make_docx, tmp_path, monkeypatch):
    watched = tmp_path / "watched"
    watched.mkdir()
    source = make_docx(paragraphs=["Hello."])
    config = make_config(tmp_path)
    monkeypatch.setattr(os, "getcwd", lambda: str(watched))
    extract_docx(io.BytesIO(source.read_bytes()), config=config)
    assert list(watched.iterdir()) == []
