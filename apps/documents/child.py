"""Entry point of the isolated extraction child process.

The child receives a generated path, a fixed format, and the code point
budget as a bounded JSON job on stdin, and answers with one length-prefixed
JSON envelope on stdout. It never writes files, never emits tracebacks, and
maps every internal failure to a safe category.
"""

import json
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

from apps.documents.canonicalization import TextOverBudget
from apps.documents.protocol import (
    INTERNAL_ERROR,
    OVER_BUDGET,
    UNSUPPORTED_FORMAT,
    max_result_bytes,
)
from apps.documents.reader import DocumentParseError, read_docx_text


@dataclass(frozen=True)
class _Job:
    path: str
    format: str
    max_code_points: int


def main() -> None:
    job = _read_job()
    envelope: dict[str, object]
    if job is None:
        envelope = {"ok": False, "category": INTERNAL_ERROR}
        max_code_points = None
    else:
        envelope = _process(job)
        max_code_points = job.max_code_points
    _emit(envelope, max_code_points)


def _read_job() -> _Job | None:
    try:
        job = json.loads(sys.stdin.buffer.read())
    except ValueError, UnicodeDecodeError:
        return None
    if not isinstance(job, dict):
        return None
    path = job.get("path")
    format_name = job.get("format")
    max_code_points = job.get("max_code_points")
    if not isinstance(path, str) or not isinstance(format_name, str):
        return None
    if not isinstance(max_code_points, int) or isinstance(max_code_points, bool):
        return None
    return _Job(path=path, format=format_name, max_code_points=max_code_points)


def _process(job: _Job) -> dict[str, object]:
    if job.format != "docx":
        return {"ok": False, "category": UNSUPPORTED_FORMAT}
    try:
        text = read_docx_text(Path(job.path), max_code_points=job.max_code_points)
    except DocumentParseError as error:
        return {"ok": False, "category": error.category}
    except TextOverBudget:
        return {"ok": False, "category": OVER_BUDGET}
    except MemoryError:
        return {"ok": False, "category": OVER_BUDGET}
    except Exception:  # noqa: BLE001 - the child must never leak a traceback
        return {"ok": False, "category": INTERNAL_ERROR}
    return {"ok": True, "text": text, "code_points": len(text)}


def _emit(envelope: dict[str, object], max_code_points: int | None) -> None:
    output = sys.stdout.buffer
    try:
        payload = json.dumps(envelope, ensure_ascii=False).encode()
        # Keep the answer within the parent's bounded pipe window.
        if max_code_points is not None and len(payload) > max_result_bytes(max_code_points):
            payload = json.dumps({"ok": False, "category": OVER_BUDGET}).encode()
        output.write(struct.pack(">Q", len(payload)))
        output.write(payload)
        output.flush()
    except OSError, ValueError, MemoryError:
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
