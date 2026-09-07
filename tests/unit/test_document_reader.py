import pytest

from apps.documents.reader import DocumentParseError, read_docx_text

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_reads_body_text_with_structure(make_docx):
    source = make_docx(headings=["Summary"], paragraphs=["First line.", "Second line."])
    text = read_docx_text(source, max_code_points=1000)
    assert text.index("# Summary") < text.index("First line.") < text.index("Second line.")


def test_includes_active_header_and_footer_text(make_docx):
    source = make_docx(paragraphs=["Body."], header_text="Confidential", footer_text="Page 1")
    text = read_docx_text(source, max_code_points=1000)
    assert "Body." in text
    assert "Confidential" in text
    assert "Page 1" in text


def test_ignores_images(make_docx):
    source = make_docx(paragraphs=["Around the image."], image=_PNG)
    text = read_docx_text(source, max_code_points=1000)
    assert "Around the image." in text
    assert "data:image" not in text
    assert "<img" not in text


def test_includes_list_items(make_docx):
    source = make_docx(paragraphs=["Intro."])
    from docx import Document

    document = Document(str(source))
    document.add_paragraph("Bullet one", style="List Bullet")
    document.save(str(source))
    text = read_docx_text(source, max_code_points=1000)
    assert "Bullet one" in text


def test_empty_document_yields_empty_text(tmp_path):
    from docx_support import build_docx

    source = build_docx(tmp_path / "empty.docx")
    text = read_docx_text(source, max_code_points=1000)
    assert text == ""


def test_rejects_non_docx_bytes_as_malformed(tmp_path):
    source = tmp_path / "junk.docx"
    source.write_bytes(b"definitely not a document")
    with pytest.raises(DocumentParseError) as raised:
        read_docx_text(source, max_code_points=1000)
    assert raised.value.category == "malformed_document"
