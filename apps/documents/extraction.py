"""The document extraction boundary.

Safely converts one supported upload into bounded canonical text in a
resource-limited child process. The container format is identified
structurally, never from the filename or declared MIME type. The source never
touches ordinary Django storage; all failures are fixed, content-safe
categories.
"""

import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from apps.documents import conf, storage, telemetry
from apps.documents.intake import (
    PDF,
    DocumentLimits,
    PackageRejected,
    detect_format,
    preflight_for_format,
)
from apps.documents.protocol import (
    EXTRACTION_UNAVAILABLE,
    INTERNAL_ERROR,
    OVER_BUDGET,
    max_result_bytes,
)
from apps.documents.runner import ProcessLimits, run_isolated

__all__ = ["DocumentExtractionError", "ExtractedDocument", "extract_document"]

_UNKNOWN_FORMAT = "unknown"


@dataclass(frozen=True)
class ExtractedDocument:
    text: str
    code_points: int


class DocumentExtractionError(Exception):
    """Extraction failed; the category is a fixed, content-safe failure."""

    def __init__(self, category: str, *, source_format: str = _UNKNOWN_FORMAT) -> None:
        super().__init__(category)
        self.category = category
        self.source_format = source_format


def extract_document(
    source: BinaryIO,
    *,
    config: conf.ExtractionSettings | None = None,
) -> ExtractedDocument:
    cfg = config or conf.current_settings()
    correlation_id = uuid.uuid4().hex
    started = time.monotonic()

    if cfg.require_linux_isolation and not _is_linux():
        telemetry.log_event(
            "document_extraction.rejected",
            correlation_id=correlation_id,
            source_format=_UNKNOWN_FORMAT,
            outcome=EXTRACTION_UNAVAILABLE,
            isolation="unavailable",
        )
        raise DocumentExtractionError(EXTRACTION_UNAVAILABLE)

    isolation = "linux" if _is_linux() else "reduced"
    telemetry.log_event(
        "document_extraction.started",
        correlation_id=correlation_id,
        source_format=_UNKNOWN_FORMAT,
        extractor_version=telemetry.EXTRACTOR_VERSION,
        isolation=isolation,
    )
    try:
        result, format_name = _extract(source, cfg, correlation_id)
    except DocumentExtractionError as failure:
        _log_completed(
            correlation_id,
            outcome=failure.category,
            code_points=None,
            started=started,
            source_format=failure.source_format,
        )
        raise
    _log_completed(
        correlation_id,
        outcome="success",
        code_points=result.code_points,
        started=started,
        source_format=format_name,
    )
    return result


def _log_completed(
    correlation_id: str,
    *,
    outcome: str,
    code_points: int | None,
    started: float,
    source_format: str,
) -> None:
    # Content-free fields only: typed outcome and bounded counts.
    telemetry.log_event(
        "document_extraction.completed",
        correlation_id=correlation_id,
        source_format=source_format,
        outcome=outcome,
        code_points=code_points,
        extractor_version=telemetry.EXTRACTOR_VERSION,
        duration_ms=int((time.monotonic() - started) * 1000),
    )


def _extract(
    source: BinaryIO,
    cfg: conf.ExtractionSettings,
    correlation_id: str,
) -> tuple[ExtractedDocument, str]:
    private_dir = storage.create_private_dir(cfg.temp_root)
    format_name = _UNKNOWN_FORMAT
    try:
        source_path = private_dir / "source.bin"
        _spool_source(source, source_path, cfg.max_upload_bytes)
        try:
            format_name = detect_format(source_path)
            preflight_for_format(
                source_path,
                format_name,
                DocumentLimits(
                    max_bytes=cfg.max_upload_bytes,
                    max_members=cfg.max_members,
                    max_expanded_bytes=cfg.max_expanded_bytes,
                    max_member_bytes=cfg.max_member_bytes,
                    max_pdf_pages=cfg.max_pdf_pages,
                ),
            )
        except PackageRejected as rejected:
            raise DocumentExtractionError(rejected.category, source_format=format_name) from None

        limits = ProcessLimits(
            cpu_seconds=cfg.cpu_seconds,
            wall_seconds=cfg.wall_seconds,
            memory_bytes=cfg.memory_bytes,
            max_result_bytes=max_result_bytes(cfg.max_code_points),
        )
        job: dict[str, str | int] = {
            "path": str(source_path),
            "format": format_name,
            "max_code_points": cfg.max_code_points,
        }
        if format_name == PDF:
            job["max_pages"] = cfg.max_pdf_pages
        outcome = run_isolated(job, limits=limits)
    finally:
        _clean_up(private_dir, correlation_id, source_format=format_name)

    if not outcome.ok or outcome.text is None:
        raise DocumentExtractionError(outcome.category or INTERNAL_ERROR, source_format=format_name)
    return ExtractedDocument(text=outcome.text, code_points=outcome.code_points or 0), format_name


def _spool_source(source: BinaryIO, path: Path, max_bytes: int) -> None:
    written = 0
    with path.open("wb") as target:
        while chunk := source.read(65536):
            written += len(chunk)
            if written > max_bytes:
                raise DocumentExtractionError(OVER_BUDGET)
            target.write(chunk)


def _clean_up(private_dir: Path, correlation_id: str, *, source_format: str) -> None:
    try:
        storage.remove_private_dir(private_dir)
    except OSError:
        telemetry.security_event(
            "document_extraction.cleanup_failed",
            correlation_id=correlation_id,
            source_format=source_format,
        )
        telemetry.mark_instance_unhealthy("cleanup_failed")
        raise DocumentExtractionError(INTERNAL_ERROR, source_format=source_format) from None


def _is_linux() -> bool:
    return sys.platform == "linux"
