"""Non-sensitive PDF fixture builders used by the extraction tests.

Fixtures are generated at test time rather than checked in, so the corpus
never contains real candidate data and stays readable in review. Text is
written through a minimal Helvetica content stream; the builders intentionally
cover the shapes the extraction boundary must accept or reject.
"""

from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
)


def build_pdf(
    path: Path,
    *,
    pages: list[str] | None = None,
    encrypt: str | None = None,
    image_only: bool = False,
    rectangles: int = 1,
) -> Path:
    writer = PdfWriter()
    if image_only:
        for _ in range(rectangles):
            _add_drawing_page(writer)
    for text in pages or []:
        _add_text_page(writer, text)
    if encrypt is not None:
        writer.encrypt(encrypt)
    with path.open("wb") as target:
        writer.write(target)
    return path


def _add_text_page(writer: PdfWriter, text: str) -> None:
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
        }
    )
    font_ref = writer._add_object(font)
    page = writer.add_blank_page(width=612, height=792)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
    )
    lines = text.splitlines() or [""]
    operators = ["BT", "/F1 12 Tf", "72 720 Td"]
    for index, line in enumerate(lines):
        if index:
            operators.append("0 -16 Td")
        operators.append(f"({_escape(line)}) Tj")
    operators.append("ET")
    stream = DecodedStreamObject()
    stream.set_data("\n".join(operators).encode("latin-1", errors="replace"))
    page[NameObject("/Contents")] = writer._add_object(stream)


def _add_drawing_page(writer: PdfWriter) -> None:
    page = writer.add_blank_page(width=612, height=792)
    stream = DecodedStreamObject()
    stream.set_data(b"0.2 0.3 0.4 rg 72 600 200 120 re f")
    page[NameObject("/Contents")] = writer._add_object(stream)


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
