import zipfile

import pytest
from docx_support import rewrite_zip

from apps.documents.intake import DocumentLimits, PackageRejected, preflight_docx

TIGHT_LIMITS = DocumentLimits(
    max_bytes=10**9,
    max_members=500,
    max_expanded_bytes=10**9,
    max_member_bytes=10**9,
)

LIMITS = DocumentLimits(
    max_bytes=10 * 1024 * 1024,
    max_members=500,
    max_expanded_bytes=64 * 1024 * 1024,
    max_member_bytes=32 * 1024 * 1024,
)

WORDPROCESSING_MAIN = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)
MACRO_ENABLED_MAIN = "application/vnd.ms-word.document.macroEnabled.main+xml"


def test_accepts_structurally_valid_docx(make_docx):
    source = make_docx(paragraphs=["Hello."])
    preflight_docx(source, LIMITS)


def test_rejects_ole_container_as_unsupported(tmp_path):
    source = tmp_path / "legacy.doc"
    source.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
    with pytest.raises(PackageRejected) as raised:
        preflight_docx(source, LIMITS)
    assert raised.value.category == "unsupported_format"


def test_rejects_non_zip_bytes_as_malformed(tmp_path):
    source = tmp_path / "junk.docx"
    source.write_bytes(b"this is not an archive at all")
    with pytest.raises(PackageRejected) as raised:
        preflight_docx(source, LIMITS)
    assert raised.value.category == "malformed_document"


def test_rejects_truncated_docx_as_malformed(make_docx):
    source = make_docx(paragraphs=["Hello."])
    truncated = source.with_name("truncated.docx")
    truncated.write_bytes(source.read_bytes()[: len(source.read_bytes()) // 2])
    with pytest.raises(PackageRejected) as raised:
        preflight_docx(truncated, LIMITS)
    assert raised.value.category == "malformed_document"


def test_rejects_encrypted_package_members_as_unsupported(make_docx):
    source = make_docx(paragraphs=["Hello."])
    encrypted = source.with_name("encrypted.docx")
    with zipfile.ZipFile(encrypted, "w") as archive:
        archive.writestr("EncryptionInfo", b"\x00" * 16)
        archive.writestr("EncryptedPackage", b"\x00" * 16)
    with pytest.raises(PackageRejected) as raised:
        preflight_docx(encrypted, LIMITS)
    assert raised.value.category == "unsupported_format"


def test_rejects_macro_enabled_main_content_type_as_unsupported(make_docx):
    source = make_docx(paragraphs=["Hello."])

    def swap_content_type(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
        data = archive.read(info)
        if info.filename == "[Content_Types].xml":
            data = data.replace(WORDPROCESSING_MAIN.encode(), MACRO_ENABLED_MAIN.encode())
        return data

    macro = rewrite_zip(source, swap_content_type)
    with pytest.raises(PackageRejected) as raised:
        preflight_docx(macro, LIMITS)
    assert raised.value.category == "unsupported_format"


def test_rejects_missing_document_part_as_malformed(tmp_path):
    source = tmp_path / "empty.docx"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
    with pytest.raises(PackageRejected) as raised:
        preflight_docx(source, LIMITS)
    assert raised.value.category == "malformed_document"


def test_rejects_member_count_over_limit(make_docx):
    source = make_docx(paragraphs=["Hello."])
    stuffed = source.with_name("stuffed.docx")
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(stuffed, "w") as archive:
        data = original.read("word/document.xml")
        for index in range(501):
            archive.writestr(f"word/media/pad{index}.bin", data)
    with pytest.raises(PackageRejected) as raised:
        preflight_docx(stuffed, TIGHT_LIMITS)
    assert raised.value.category == "over_budget"


def test_rejects_expansion_over_limit(make_docx):
    source = make_docx(paragraphs=["Hello."])
    with pytest.raises(PackageRejected) as raised:
        preflight_docx(
            source,
            DocumentLimits(
                max_bytes=10**9, max_members=500, max_expanded_bytes=10, max_member_bytes=10**9
            ),
        )
    assert raised.value.category == "over_budget"


def test_rejects_hostile_member_paths(tmp_path):
    source = tmp_path / "hostile.docx"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("../evil.xml", b"data")
    with pytest.raises(PackageRejected) as raised:
        preflight_docx(source, LIMITS)
    assert raised.value.category == "malformed_document"


def test_rejects_oversized_file(make_docx):
    source = make_docx(paragraphs=["Hello."])
    with pytest.raises(PackageRejected) as raised:
        preflight_docx(
            source,
            DocumentLimits(
                max_bytes=10, max_members=500, max_expanded_bytes=10**9, max_member_bytes=10**9
            ),
        )
    assert raised.value.category == "over_budget"
