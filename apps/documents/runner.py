"""Supervision of the isolated extraction child process.

The child runs in its own session (and therefore its own process group) under
hard resource limits: CPU seconds, address space, and a wall-clock deadline
enforced by the parent. The result travels through a bounded pipe as a
length-prefixed JSON envelope. On every path the process group is terminated
before the parent returns.
"""

import json
import os
import resource
import select
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

__all__ = ["ChildOutcome", "ProcessLimits", "run_isolated"]

READ_CHUNK = 65536
ENVELOPE_HEADER = 8
_JOB_BYTES = 65536

_SAFE_CATEGORIES = frozenset(
    {
        "unsupported_format",
        "malformed_document",
        "over_budget",
        "processing_timeout",
        "extraction_unavailable",
        "internal_error",
    }
)

_INTERNAL_ERROR = "internal_error"
_OVER_BUDGET = "over_budget"
_TIMEOUT = "processing_timeout"

DEFAULT_CHILD_COMMAND: tuple[str, ...] = (sys.executable, "-I", "-m", "apps.documents.child")


@dataclass(frozen=True)
class ProcessLimits:
    cpu_seconds: int
    wall_seconds: float
    memory_bytes: int
    max_result_bytes: int


@dataclass(frozen=True)
class ChildOutcome:
    ok: bool
    category: str | None = None
    text: str | None = None
    code_points: int | None = None


def run_isolated(
    job: Mapping[str, str | int],
    *,
    limits: ProcessLimits,
    child_command: Sequence[str] | None = None,
) -> ChildOutcome:
    command = list(child_command or DEFAULT_CHILD_COMMAND)
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        # A sync, single-threaded caller makes the fork-time limit installation
        # safe on platforms without posix_spawn rlimit support.
        preexec_fn=_limit_child_factory(limits),  # noqa: PLW1509
    )
    try:
        return _supervise(process, job, limits)
    finally:
        _terminate_group(process)


def _supervise(
    process: subprocess.Popen[bytes],
    job: Mapping[str, str | int],
    limits: ProcessLimits,
) -> ChildOutcome:
    if process.stdin is None or process.stdout is None:  # pragma: no cover - always piped
        return ChildOutcome(ok=False, category=_INTERNAL_ERROR)
    deadline = time.monotonic() + limits.wall_seconds

    try:
        process.stdin.write(json.dumps(job).encode()[:_JOB_BYTES])
        process.stdin.close()
    except OSError:
        pass

    output = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return ChildOutcome(ok=False, category=_TIMEOUT)
        ready, _, _ = select.select([process.stdout.fileno()], [], [], remaining)
        if not ready:
            return ChildOutcome(ok=False, category=_TIMEOUT)
        chunk = os.read(process.stdout.fileno(), READ_CHUNK)
        if not chunk:
            break
        output.extend(chunk)
        if len(output) > limits.max_result_bytes:
            return ChildOutcome(ok=False, category=_OVER_BUDGET)

    remaining = deadline - time.monotonic()
    try:
        process.wait(timeout=max(remaining, 0.1))
    except subprocess.TimeoutExpired:
        return ChildOutcome(ok=False, category=_TIMEOUT)

    if process.returncode != 0:
        if process.returncode == -signal.SIGXCPU:
            return ChildOutcome(ok=False, category=_OVER_BUDGET)
        return ChildOutcome(ok=False, category=_INTERNAL_ERROR)
    return _parse_envelope(bytes(output), limits)


def _parse_envelope(output: bytes, limits: ProcessLimits) -> ChildOutcome:
    if len(output) < ENVELOPE_HEADER:
        return ChildOutcome(ok=False, category=_INTERNAL_ERROR)
    length = int.from_bytes(output[:ENVELOPE_HEADER], "big")
    if length > limits.max_result_bytes or len(output) != ENVELOPE_HEADER + length:
        return ChildOutcome(ok=False, category=_INTERNAL_ERROR)
    try:
        envelope = json.loads(output[ENVELOPE_HEADER:])
    except ValueError, UnicodeDecodeError:
        return ChildOutcome(ok=False, category=_INTERNAL_ERROR)

    if envelope.get("ok") is True:
        text = envelope.get("text")
        code_points = envelope.get("code_points")
        if not isinstance(text, str) or not isinstance(code_points, int):
            return ChildOutcome(ok=False, category=_INTERNAL_ERROR)
        if code_points != len(text) or code_points > 10_000_000:
            return ChildOutcome(ok=False, category=_INTERNAL_ERROR)
        return ChildOutcome(ok=True, text=text, code_points=code_points)

    category = envelope.get("category")
    if isinstance(category, str) and category in _SAFE_CATEGORIES:
        return ChildOutcome(ok=False, category=category)
    return ChildOutcome(ok=False, category=_INTERNAL_ERROR)


def _limit_child_factory(limits: ProcessLimits) -> Callable[[], None]:
    def apply_limits() -> None:
        cpu = limits.cpu_seconds
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 2))
        # The child must never write files; any write attempt kills it.
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
        try:
            resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))
        except OSError, ValueError:
            # Darwin cannot lower RLIMIT_AS; Linux enforces it. Reduced
            # memory isolation on other platforms is disclosed by telemetry
            # and fails closed in production configuration.
            pass

    return apply_limits


def _terminate_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError, PermissionError, OSError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:  # pragma: no cover - SIGKILL is unconditional
        pass
    for stream in (process.stdin, process.stdout):
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass
