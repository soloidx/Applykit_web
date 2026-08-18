import pytest

from apps.cover_letters.content import cover_letter_visible_text, sanitize_cover_letter_html

pytestmark = pytest.mark.unit


def test_cover_letter_sanitizer_keeps_narrow_canonical_html_and_visible_text() -> None:
    sanitized = sanitize_cover_letter_html(
        '<div class="unsafe"><strong>Hello</strong> '
        '<a href="javascript:alert(1)" target="_blank">team</a>'
        '<script>ignored()</script><a href="https://example.com" style="color:red">link</a></div>'
    )

    assert sanitized == (
        "<strong>Hello</strong> team"
        '<a href="https://example.com" rel="nofollow noopener noreferrer">link</a>'
    )
    assert (
        sanitize_cover_letter_html('<ol><li data-list="bullet">Item</li></ol>')
        == "<ul><li>Item</li></ul>"
    )
    assert cover_letter_visible_text("<p> \n </p><p>Visible text</p>") == " \n Visible text"
