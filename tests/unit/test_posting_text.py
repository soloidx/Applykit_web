import unicodedata

import pytest

from apps.applications.posting_text import (
    MAX_POSTING_CODE_POINTS,
    PostingTextRejected,
    normalize_posting_text,
)


def test_normalizes_to_nfc():
    assert normalize_posting_text("cafe\u0301") == unicodedata.normalize("NFC", "cafe\u0301")


def test_normalizes_line_endings():
    assert normalize_posting_text("a\r\nb\rc") == "a\nb\nc"


def test_strips_unsafe_controls_and_preserves_structure():
    text = "Role\x00: Engineer\u202e\n\nBuild \x7fservices with Node.js."
    assert normalize_posting_text(text) == "Role: Engineer\n\nBuild services with Node.js."


def test_rejects_empty_and_whitespace_only_text():
    for value in ("", "   ", "\n\n\t", "\x00\x1f"):
        with pytest.raises(PostingTextRejected):
            normalize_posting_text(value)


def test_accepts_a_short_posting_without_a_minimum_length_heuristic():
    assert normalize_posting_text("Dev") == "Dev"


def test_rejects_text_over_the_hard_bound_without_truncation():
    with pytest.raises(PostingTextRejected):
        normalize_posting_text("a" * (MAX_POSTING_CODE_POINTS + 1))


def test_allows_text_at_the_exact_bound():
    text = "a" * MAX_POSTING_CODE_POINTS
    assert normalize_posting_text(text) == text
