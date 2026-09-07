"""Linux-container isolation tests for the document extraction boundary.

These tests only run on Linux, where RLIMIT_AS is enforceable and the
production isolation path is available. They are part of the
release-gating container suite.
"""

import io
import sys

import pytest
from docx_support import build_docx

from apps.documents.conf import ExtractionSettings
from apps.documents.extraction import extract_docx
from apps.documents.runner import ProcessLimits, run_isolated

pytestmark = [
    pytest.mark.linux_container,
    pytest.mark.skipif(sys.platform != "linux", reason="requires Linux container isolation"),
]


def test_production_isolation_gate_allows_linux(tmp_path):
    source = build_docx(tmp_path / "source.docx", paragraphs=["Container body text."])
    config = ExtractionSettings(
        max_upload_bytes=10 * 1024 * 1024,
        max_members=500,
        max_expanded_bytes=64 * 1024 * 1024,
        max_member_bytes=32 * 1024 * 1024,
        max_code_points=100_000,
        cpu_seconds=10,
        wall_seconds=15.0,
        memory_bytes=512 * 1024 * 1024,
        temp_root=tmp_path / "private",
        require_linux_isolation=True,
    )
    result = extract_docx(io.BytesIO(source.read_bytes()), config=config)
    assert "Container body text." in result.text


def test_memory_limit_is_enforced_in_container():
    command = [sys.executable, "-c", "bytearray(1024 * 1024 * 1024)"]
    limits = ProcessLimits(
        cpu_seconds=10,
        wall_seconds=30.0,
        memory_bytes=256 * 1024 * 1024,
        max_result_bytes=65536,
    )
    outcome = run_isolated({}, limits=limits, child_command=command)
    assert outcome.ok is False
    assert outcome.category == "internal_error"
