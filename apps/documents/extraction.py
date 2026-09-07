"""The document extraction boundary.

Safely converts one supported DOCX upload into bounded canonical text in a
resource-limited child process. The source never touches ordinary Django
storage; all failures are fixed, content-safe categories.
"""

import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from apps.documents import conf, storage, telemetry
from apps.documents.intake import DocumentLimits, PackageRejected, preflight_docx
from apps.documents.protocol import (
    EXTRACTION_UNAVAILABLE,
    INTERNAL_ERROR,
    OVER_BUDGET,
    max_result_bytes,
)
from apps.documents.runner import ProcessLimits, run_isolated

__all__ = ["DocumentExtractionError", "ExtractedDocument", "extract_docx"]


@dataclass(frozen=True)
class ExtractedDocument:
    text: str
    code_points: int


class DocumentExtractionError(Exception):
    """Extraction failed; the category is a fixed, content-safe failure."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def extract_docx(
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
            source_format="docx",
            outcome=EXTRACTION_UNAVAILABLE,
            isolation="unavailable",
        )
        raise DocumentExtractionError(EXTRACTION_UNAVAILABLE)

    isolation = "linux" if _is_linux() else "reduced"
    telemetry.log_event(
        "document_extraction.started",
        correlation_id=correlation_id,
        source_format="docx",
        extractor_version=telemetry.EXTRACTOR_VERSION,
        isolation=isolation,
    )
    try:
        result = _extract(source, cfg, correlation_id)
    except DocumentExtractionError as failure:
        _log_completed(correlation_id, outcome=failure.category, code_points=None, started=started)
        raise
    _log_completed(
        correlation_id, outcome="success", code_points=result.code_points, started=started
    )
    return result


def _log_completed(
    correlation_id: str, *, outcome: str, code_points: int | None, started: float
) -> None:
    # Content-free fields only: typed outcome and bounded counts.
    telemetry.log_event(
        "document_extraction.completed",
        correlation_id=correlation_id,
        source_format="docx",
        outcome=outcome,
        code_points=code_points,
        extractor_version=telemetry.EXTRACTOR_VERSION,
        duration_ms=int((time.monotonic() - started) * 1000),
    )


def _extract(
    source: BinaryIO, cfg: conf.ExtractionSettings, correlation_id: str
) -> ExtractedDocument:
    private_dir = storage.create_private_dir(cfg.temp_root)
    try:
        source_path = private_dir / "source.docx"
        _spool_source(source, source_path, cfg.max_upload_bytes)
        try:
            preflight_docx(
                source_path,
                DocumentLimits(
                    max_bytes=cfg.max_upload_bytes,
                    max_members=cfg.max_members,
                    max_expanded_bytes=cfg.max_expanded_bytes,
                    max_member_bytes=cfg.max_member_bytes,
                ),
            )
        except PackageRejected as rejected:
            raise DocumentExtractionError(rejected.category) from None

        limits = ProcessLimits(
            cpu_seconds=cfg.cpu_seconds,
            wall_seconds=cfg.wall_seconds,
            memory_bytes=cfg.memory_bytes,
            max_result_bytes=max_result_bytes(cfg.max_code_points),
        )
        job: dict[str, str | int] = {
            "path": str(source_path),
            "format": "docx",
            "max_code_points": cfg.max_code_points,
        }
        outcome = run_isolated(job, limits=limits)
    finally:
        _clean_up(private_dir, correlation_id)

    if not outcome.ok or outcome.text is None:
        raise DocumentExtractionError(outcome.category or INTERNAL_ERROR)
    return ExtractedDocument(text=outcome.text, code_points=outcome.code_points or 0)


def _spool_source(source: BinaryIO, path: Path, max_bytes: int) -> None:
    written = 0
    with path.open("wb") as target:
        while chunk := source.read(65536):
            written += len(chunk)
            if written > max_bytes:
                raise DocumentExtractionError(OVER_BUDGET)
            target.write(chunk)


def _clean_up(private_dir: Path, correlation_id: str) -> None:
    try:
        storage.remove_private_dir(private_dir)
    except OSError:
        telemetry.security_event(
            "document_extraction.cleanup_failed",
            correlation_id=correlation_id,
            source_format="docx",
        )
        telemetry.mark_instance_unhealthy("cleanup_failed")
        raise DocumentExtractionError(INTERNAL_ERROR) from None


def _is_linux() -> bool:
    return sys.platform == "linux"
