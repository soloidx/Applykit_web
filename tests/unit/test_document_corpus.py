"""Acceptance corpus and hostile fixtures for the document extraction boundary.

This is the release-gating corpus: representative, non-sensitive documents the
feature must read faithfully, plus hostile fixtures it must reject safely. It
exercises extraction fidelity, resource limits, cleanup, and content
non-disclosure across both DOCX and PDF through the real child process.
"""

import io
import logging
import sys
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from docx_support import build_docx
from pdf_support import build_pdf

from apps.documents import telemetry
from apps.documents.conf import ExtractionSettings
from apps.documents.extraction import DocumentExtractionError, extract_document
from apps.documents.runner import ProcessLimits, run_isolated


def corpus_config(tmp_path: Path, **overrides: object) -> ExtractionSettings:
    values: dict[str, object] = {
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
        "max_pdf_pages": 50,
    }
    values.update(overrides)
    return ExtractionSettings(**values)  # type: ignore[arg-type]


@dataclass(frozen=True)
class AcceptanceDocument:
    name: str
    build: Callable[[Path], Path]
    expected: tuple[str, ...]


ACCEPTANCE_CORPUS: tuple[AcceptanceDocument, ...] = (
    AcceptanceDocument(
        name="docx-structured-resume",
        build=lambda path: build_docx(
            path / "resume.docx",
            headings=["Summary", "Experience"],
            paragraphs=[
                "Jane Doe is a platform engineer.",
                "Built reliable developer tooling.",
            ],
            header_text="Jane Doe - Confidential",
            footer_text="Page 1",
        ),
        expected=("Summary", "Experience", "Jane Doe is a platform engineer.", "Page 1"),
    ),
    AcceptanceDocument(
        name="docx-short-cover-letter",
        build=lambda path: build_docx(
            path / "letter.docx",
            paragraphs=["Dear hiring team,", "I am excited to apply.", "Sincerely, Jane Doe"],
        ),
        expected=("Dear hiring team,", "Sincerely, Jane Doe"),
    ),
    AcceptanceDocument(
        name="pdf-single-page",
        build=lambda path: build_pdf(
            path / "single.pdf",
            pages=["Jane Doe\nPlatform Engineer\nLondon"],
        ),
        expected=("Jane Doe", "Platform Engineer", "London"),
    ),
    AcceptanceDocument(
        name="pdf-multi-page",
        build=lambda path: build_pdf(
            path / "multi.pdf",
            pages=[
                "Jane Doe\nPlatform Engineer",
                "Experience\nBuilt reliable developer tooling.",
                "Education\nUniversity of London",
            ],
        ),
        expected=("Experience", "Built reliable developer tooling.", "University of London"),
    ),
)


@pytest.mark.parametrize("document", ACCEPTANCE_CORPUS, ids=lambda doc: doc.name)
def test_acceptance_corpus_is_read_faithfully(document: AcceptanceDocument, tmp_path, caplog):
    source = document.build(tmp_path)
    config = corpus_config(tmp_path)
    with caplog.at_level(logging.DEBUG):
        result = extract_document(io.BytesIO(source.read_bytes()), config=config)

    for expected in document.expected:
        assert expected in result.text
    assert result.code_points == len(result.text)
    assert list((tmp_path / "private").iterdir()) == []
    for record in caplog.records:
        for expected in document.expected:
            assert expected not in record.getMessage()


def _macro_enabled_docx(path: Path) -> Path:
    source = build_docx(path / "macro.docx", paragraphs=["Hello."])
    from docx_support import rewrite_zip

    def swap(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
        data = archive.read(info)
        if info.filename == "[Content_Types].xml":
            data = data.replace(
                b"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
                b"application/vnd.ms-word.document.macroEnabled.main+xml",
            )
        return data

    return rewrite_zip(source, swap)


@dataclass(frozen=True)
class HostileFixture:
    name: str
    build: Callable[[Path], Path]
    category: str


HOSTILE_FIXTURES: tuple[HostileFixture, ...] = (
    HostileFixture(
        name="encrypted-pdf",
        build=lambda path: build_pdf(path / "encrypted.pdf", pages=["Secret"], encrypt="pw"),
        category="unsupported_format",
    ),
    HostileFixture(
        name="image-only-pdf",
        build=lambda path: build_pdf(path / "image.pdf", image_only=True),
        category="unsupported_format",
    ),
    HostileFixture(
        name="macro-enabled-docx",
        build=_macro_enabled_docx,
        category="unsupported_format",
    ),
    HostileFixture(
        name="ole-compound-document",
        build=lambda path: _write(
            path, "legacy.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64
        ),
        category="unsupported_format",
    ),
    HostileFixture(
        name="junk-bytes",
        build=lambda path: _write(path, "junk.bin", b"definitely not a document"),
        category="malformed_document",
    ),
    HostileFixture(
        name="truncated-pdf",
        build=lambda path: _truncated(build_pdf(path / "truncated.pdf", pages=["Jane Doe"])),
        category="malformed_document",
    ),
    HostileFixture(
        name="truncated-docx",
        build=lambda path: _truncated(build_docx(path / "truncated.docx", paragraphs=["Jane Doe"])),
        category="malformed_document",
    ),
)


def _write(path: Path, name: str, data: bytes) -> Path:
    target = path / name
    target.write_bytes(data)
    return target


def _truncated(source: Path) -> Path:
    source.write_bytes(source.read_bytes()[: len(source.read_bytes()) // 2])
    return source


@pytest.mark.parametrize("fixture", HOSTILE_FIXTURES, ids=lambda fixture: fixture.name)
def test_hostile_fixtures_fail_safely_and_leave_nothing(fixture: HostileFixture, tmp_path, caplog):
    source = fixture.build(tmp_path)
    config = corpus_config(tmp_path)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(DocumentExtractionError) as raised:
            extract_document(io.BytesIO(source.read_bytes()), config=config)

    assert raised.value.category == fixture.category
    assert list((tmp_path / "private").iterdir()) == []
    assert telemetry.is_instance_healthy() is True


def test_page_and_output_limits_are_enforced(tmp_path):
    many_pages = build_pdf(tmp_path / "many.pdf", pages=[f"Page {index}" for index in range(51)])
    with pytest.raises(DocumentExtractionError) as page_failure:
        extract_document(io.BytesIO(many_pages.read_bytes()), config=corpus_config(tmp_path))
    assert page_failure.value.category == "over_budget"

    long_text = build_pdf(tmp_path / "long.pdf", pages=["word " * 200])
    with pytest.raises(DocumentExtractionError) as text_failure:
        extract_document(
            io.BytesIO(long_text.read_bytes()),
            config=corpus_config(tmp_path, max_code_points=100),
        )
    assert text_failure.value.category == "over_budget"


def test_corpus_gate_terminates_a_timed_out_child():
    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    limits = ProcessLimits(
        cpu_seconds=10,
        wall_seconds=1.0,
        memory_bytes=512 * 1024 * 1024,
        max_result_bytes=65536,
    )
    outcome = run_isolated({"path": "unused"}, limits=limits, child_command=command)
    assert outcome.ok is False
    assert outcome.category == "processing_timeout"
