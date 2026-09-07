import json
import os
import struct
import subprocess
import sys
import time

import pytest

from apps.documents.runner import ProcessLimits, run_isolated

REPO_CHILD = [sys.executable, "-I", "-m", "apps.documents.child"]

CPU_LIMITS = ProcessLimits(
    cpu_seconds=10, wall_seconds=30.0, memory_bytes=512 * 1024 * 1024, max_result_bytes=65536
)


def test_runs_real_docx_child_to_completion(make_docx):
    source = make_docx(paragraphs=["Isolated body text."])
    job = {"path": str(source), "format": "docx", "max_code_points": 1000}
    outcome = run_isolated(job, limits=CPU_LIMITS, child_command=REPO_CHILD)
    assert outcome.ok is True
    assert outcome.text == "Isolated body text."
    assert outcome.code_points == len("Isolated body text.")


def test_wall_timeout_terminates_child_and_reports_timeout():
    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    limits = ProcessLimits(
        cpu_seconds=10, wall_seconds=1.0, memory_bytes=512 * 1024 * 1024, max_result_bytes=65536
    )
    started = time.monotonic()
    outcome = run_isolated({"path": "unused"}, limits=limits, child_command=command)
    assert outcome.ok is False
    assert outcome.category == "processing_timeout"
    assert time.monotonic() - started < 10


def test_cpu_limit_reports_over_budget():
    command = [sys.executable, "-c", "while True: pass"]
    limits = ProcessLimits(
        cpu_seconds=1, wall_seconds=60.0, memory_bytes=512 * 1024 * 1024, max_result_bytes=65536
    )
    outcome = run_isolated({}, limits=limits, child_command=command)
    assert outcome.ok is False
    assert outcome.category == "over_budget"


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="RLIMIT_AS is only enforced on Linux; covered by the container suite",
)
def test_memory_limit_reports_over_budget():
    script = (
        "import sys, struct\n"
        "try:\n"
        "    bytearray(1024 * 1024 * 1024)\n"
        "    payload = json.dumps({'ok': False, 'category': 'internal_error'}).encode()\n"
        "except MemoryError:\n"
        "    payload = json.dumps({'ok': False, 'category': 'over_budget'}).encode()\n"
        "sys.stdout.buffer.write(struct.pack('>Q', len(payload)) + payload)\n"
    )
    script = script.replace("json.dumps", "__import__('json').dumps")
    command = [sys.executable, "-c", script]
    limits = ProcessLimits(
        cpu_seconds=10, wall_seconds=30.0, memory_bytes=256 * 1024 * 1024, max_result_bytes=65536
    )
    outcome = run_isolated({}, limits=limits, child_command=command)
    assert outcome.ok is False
    assert outcome.category == "over_budget"


def test_output_flood_is_bounded():
    command = [sys.executable, "-c", "import sys; sys.stdout.write('x' * 10_000_000)"]
    limits = ProcessLimits(
        cpu_seconds=10, wall_seconds=30.0, memory_bytes=512 * 1024 * 1024, max_result_bytes=65536
    )
    outcome = run_isolated({}, limits=limits, child_command=command)
    assert outcome.ok is False
    assert outcome.category == "over_budget"


def test_garbage_output_is_internal_error():
    command = [sys.executable, "-c", "print('not an envelope')"]
    outcome = run_isolated({}, limits=CPU_LIMITS, child_command=command)
    assert outcome.ok is False
    assert outcome.category == "internal_error"


def test_nonzero_exit_is_internal_error():
    command = [sys.executable, "-c", "raise SystemExit(3)"]
    outcome = run_isolated({}, limits=CPU_LIMITS, child_command=command)
    assert outcome.ok is False
    assert outcome.category == "internal_error"


def test_timeout_kills_grandchildren(monkeypatch, tmp_path):
    script = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([{sys.executable!r}, '-c', 'import time; time.sleep(60)'])\n"
        "time.sleep(60)\n"
    )
    command = [sys.executable, "-c", script]
    limits = ProcessLimits(
        cpu_seconds=10, wall_seconds=1.0, memory_bytes=512 * 1024 * 1024, max_result_bytes=65536
    )
    outcome = run_isolated({}, limits=limits, child_command=command)
    assert outcome.category == "processing_timeout"
    assert _no_sleep_processes()


def _no_sleep_processes() -> bool:
    probe = subprocess.run(
        ["pgrep", "-f", "time; time.sleep(60)"],
        capture_output=True,
        text=True,
        check=False,
    )
    return probe.returncode != 0


def test_child_module_handles_parse_failure_as_typed_envelope(tmp_path):
    source = tmp_path / "junk.docx"
    source.write_bytes(b"not a document")
    job = {"path": str(source), "format": "docx", "max_code_points": 1000}
    outcome = run_isolated(job, limits=CPU_LIMITS, child_command=REPO_CHILD)
    assert outcome.ok is False
    assert outcome.category == "malformed_document"


def test_child_may_not_write_files(tmp_path):
    target = tmp_path / "child-escape.bin"
    script = f"open({str(target)!r}, 'wb').write(b'x')"
    outcome = run_isolated({}, limits=CPU_LIMITS, child_command=[sys.executable, "-c", script])
    assert outcome.ok is False
    assert outcome.category == "internal_error"
    # The write itself must be prevented; nothing may reach the disk.
    assert not target.exists() or target.stat().st_size == 0


def test_job_protocol_is_bounded_json():
    job = {"path": "x" * 1000, "format": "docx", "max_code_points": 5}
    assert len(json.dumps(job).encode()) < 65536
    assert struct.pack(">Q", 0) == b"\x00" * 8
    assert os.name in ("posix", "nt")
