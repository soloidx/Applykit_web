"""Local normalization for candidate-pasted job posting text.

Pasted text is untrusted. It is normalized to NFC, has its line endings
normalized, and has unsafe control characters removed before it can reach an AI
operation. It is rejected, never truncated, when it is empty after
normalization or exceeds the hard code-point bound. There is no keyword or
minimum-length heuristic: a short posting is valid.
"""

from __future__ import annotations

from apps.documents.canonicalization import TextOverBudget, canonicalize

__all__ = [
    "MAX_POSTING_CODE_POINTS",
    "PostingTextRejected",
    "normalize_posting_text",
]

MAX_POSTING_CODE_POINTS = 50_000


class PostingTextRejected(Exception):
    """The pasted text is empty after normalization or over its bound."""


def normalize_posting_text(raw_text: str) -> str:
    """Return bounded canonical posting text or raise :class:`PostingTextRejected`."""

    text = raw_text if isinstance(raw_text, str) else ""
    try:
        canonical = canonicalize(text, max_code_points=MAX_POSTING_CODE_POINTS)
    except TextOverBudget:
        raise PostingTextRejected from None
    if not canonical:
        raise PostingTextRejected
    return canonical
