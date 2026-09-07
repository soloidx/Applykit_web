"""Typed access to the document extraction configuration.

Callers cannot override the configured limits; values come only from Django
settings, which operators control.
"""

import tempfile
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

__all__ = ["ExtractionSettings", "current_settings"]


@dataclass(frozen=True)
class ExtractionSettings:
    max_upload_bytes: int
    max_members: int
    max_expanded_bytes: int
    max_member_bytes: int
    max_code_points: int
    cpu_seconds: int
    wall_seconds: float
    memory_bytes: int
    temp_root: Path
    require_linux_isolation: bool


def current_settings() -> ExtractionSettings:
    configured = getattr(settings, "DOCUMENT_EXTRACTION", {})
    temp_root = configured.get("TEMP_ROOT") or Path(tempfile.gettempdir()) / "applykit-documents"
    return ExtractionSettings(
        max_upload_bytes=int(configured.get("MAX_UPLOAD_BYTES", 10 * 1024 * 1024)),
        max_members=int(configured.get("MAX_MEMBERS", 2000)),
        max_expanded_bytes=int(configured.get("MAX_EXPANDED_BYTES", 256 * 1024 * 1024)),
        max_member_bytes=int(configured.get("MAX_MEMBER_BYTES", 64 * 1024 * 1024)),
        max_code_points=int(configured.get("MAX_TEXT_CODE_POINTS", 100_000)),
        cpu_seconds=int(configured.get("CPU_SECONDS", 10)),
        wall_seconds=float(configured.get("WALL_SECONDS", 15)),
        memory_bytes=int(configured.get("MEMORY_BYTES", 512 * 1024 * 1024)),
        temp_root=Path(temp_root),
        require_linux_isolation=bool(configured.get("REQUIRE_LINUX_ISOLATION", False)),
    )
