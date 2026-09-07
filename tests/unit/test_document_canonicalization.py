import unicodedata

import pytest

from apps.documents.canonicalization import TextOverBudget, canonicalize


def test_normalizes_to_nfc():
    composed = canonicalize("cafe\u0301", max_code_points=100)
    assert composed == unicodedata.normalize("NFC", "cafe\u0301")


def test_normalizes_line_endings():
    assert canonicalize("a\r\nb\rc", max_code_points=100) == "a\nb\nc"


def test_removes_unsafe_control_characters():
    assert canonicalize("a\x00b\x1fc\x7fd", max_code_points=100) == "abcd"


def test_removes_bidi_and_zero_width_controls():
    text = "a\u202eb\u2066c\ufeffd\u200be\u2060f"
    assert canonicalize(text, max_code_points=100) == "abcdef"


def test_converts_tab_to_space():
    assert canonicalize("a\tb", max_code_points=100) == "a b"


def test_preserves_newlines_and_paragraph_separation():
    text = "Heading\n\nParagraph one.\nParagraph two."
    assert canonicalize(text, max_code_points=100) == text


def test_collapses_excessive_blank_lines():
    assert canonicalize("a\n\n\n\n\nb", max_code_points=100) == "a\n\nb"


def test_strips_surrounding_whitespace():
    assert canonicalize("  \n a \n\n ", max_code_points=100) == "a"


def test_rejects_text_over_code_point_budget():
    with pytest.raises(TextOverBudget):
        canonicalize("a" * 101, max_code_points=100)


def test_allows_text_at_exact_budget():
    assert canonicalize("a" * 100, max_code_points=100) == "a" * 100
