"""The bounded IPC protocol shared by the extraction parent and child.

Failure categories, envelope framing, and pipe bounds live here so the two
ends of the pipe cannot drift apart.
"""

__all__ = [
    "ENVELOPE_HEADER",
    "JOB_MAX_BYTES",
    "RESULT_SLACK_BYTES",
    "SAFE_CATEGORIES",
    "max_result_bytes",
]

ENVELOPE_HEADER = 8
JOB_MAX_BYTES = 65536
RESULT_SLACK_BYTES = 65536

# Fixed, content-safe failure categories. Nothing outside this set may be
# reported to a candidate.
UNSUPPORTED_FORMAT = "unsupported_format"
MALFORMED_DOCUMENT = "malformed_document"
OVER_BUDGET = "over_budget"
PROCESSING_TIMEOUT = "processing_timeout"
EXTRACTION_UNAVAILABLE = "extraction_unavailable"
INTERNAL_ERROR = "internal_error"

SAFE_CATEGORIES = frozenset(
    {
        UNSUPPORTED_FORMAT,
        MALFORMED_DOCUMENT,
        OVER_BUDGET,
        PROCESSING_TIMEOUT,
        EXTRACTION_UNAVAILABLE,
        INTERNAL_ERROR,
    }
)


def max_result_bytes(max_code_points: int) -> int:
    """Largest possible envelope: UTF-8 needs at most 4 bytes per code point."""
    return max_code_points * 4 + RESULT_SLACK_BYTES
