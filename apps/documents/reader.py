"""Narrow DOCX text extraction running inside the isolated child process.

Mammoth reads the body text; a narrow python-docx traversal collects the
active (non-linked) headers and footers. The result is canonicalized to a
bounded, structured plain-text form. All parse failures are mapped to fixed,
content-safe categories.
"""

import zipfile
from html.parser import HTMLParser
from pathlib import Path

import mammoth
from docx import Document

from apps.documents.canonicalization import canonicalize

__all__ = ["DocumentParseError", "read_docx_text"]

_CATEGORY_MALFORMED = "malformed_document"

_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_BLOCK_TAGS = _HEADING_TAGS | {"p", "li", "tr"}
_CELL_TAGS = frozenset({"td", "th"})


class DocumentParseError(Exception):
    """The document could not be parsed; carries a safe failure category."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


class _BlockTextParser(HTMLParser):
    """Collects headings, paragraphs, list items, and table rows as blocks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._buffer: list[str] = []
        self._heading_level: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _HEADING_TAGS:
            self._flush()
            self._heading_level = int(tag[1])
        elif tag in _BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in _CELL_TAGS:
            self._buffer.append(" ")
        elif tag in _BLOCK_TAGS or tag in _HEADING_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        self._buffer.append(data)

    def _flush(self) -> None:
        text = "".join(self._buffer).strip()
        self._buffer = []
        if not text:
            return
        if self._heading_level is not None:
            self.blocks.append(f"{'#' * self._heading_level} {text}")
            self._heading_level = None
        else:
            self.blocks.append(text)


def read_docx_text(path: Path, *, max_code_points: int) -> str:
    body_blocks = _read_body(path)
    header_footer_lines = _read_headers_and_footers(path)

    sections = "\n\n".join(body_blocks)
    if header_footer_lines:
        rendered = "\n\n".join(filter(None, (sections, "\n".join(header_footer_lines))))
    else:
        rendered = sections
    return canonicalize(rendered, max_code_points=max_code_points)


def _read_body(path: Path) -> list[str]:
    parser = _BlockTextParser()
    try:
        with path.open("rb") as source:
            # Images contribute no text; the empty src avoids decoding image
            # data into memory.
            result = mammoth.convert_to_html(
                source,
                convert_image=mammoth.images.img_element(lambda image: {"src": ""}),
            )
    except (zipfile.BadZipFile, OSError, ValueError, KeyError) as error:
        raise DocumentParseError(_CATEGORY_MALFORMED) from error
    parser.feed(result.value)
    return parser.blocks


def _read_headers_and_footers(path: Path) -> list[str]:
    try:
        document = Document(str(path))
    except (zipfile.BadZipFile, OSError, ValueError, KeyError) as error:
        raise DocumentParseError(_CATEGORY_MALFORMED) from error

    lines: list[str] = []
    for section in document.sections:
        for part in (
            section.header,
            section.first_page_header,
            section.even_page_header,
            section.footer,
            section.first_page_footer,
            section.even_page_footer,
        ):
            if part.is_linked_to_previous:
                continue
            for paragraph in part.paragraphs:
                text = paragraph.text.strip()
                if text and text not in lines:
                    lines.append(text)
    return lines
