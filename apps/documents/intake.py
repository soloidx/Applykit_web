"""Structural intake validation for supported document uploads.

The source document is never trusted by filename or declared MIME type. The
container itself is identified structurally, then preflighted against
configured limits before any parsing work is attempted. DOCX packages are
checked for member and expansion bounds; PDFs are checked for a well-formed,
unencrypted, complete container. Page and text bounds are enforced inside the
isolated child process that does the actual parsing.
"""

import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from apps.documents.protocol import MALFORMED_DOCUMENT, OVER_BUDGET, UNSUPPORTED_FORMAT

__all__ = [
    "DOCX",
    "PDF",
    "DocumentLimits",
    "PackageRejected",
    "detect_format",
    "preflight_docx",
    "preflight_for_format",
    "preflight_pdf",
]

DOCX = "docx"
PDF = "pdf"

_OLE_MAGIC = b"\xd0\xcf\x11\xe0"
_ZIP_MAGIC = b"PK"
_PDF_MAGIC = b"%PDF-"
_PDF_EOF_MARKER = b"%%EOF"
_PDF_ENCRYPT_MARKER = b"/Encrypt"
_HEADER_WINDOW = 1024
_PDF_TRAILER_WINDOW = 4096
_ENCRYPTED_MEMBERS = frozenset({"encryptioninfo", "encryptedpackage"})
_CONTENT_TYPES_PART = "[Content_Types].xml"
_DOCUMENT_PART = "word/document.xml"
_WORDPROCESSING_MAIN = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)
_CONTENT_TYPES_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/content-types"


@dataclass(frozen=True)
class DocumentLimits:
    max_bytes: int
    max_members: int
    max_expanded_bytes: int
    max_member_bytes: int
    max_pdf_pages: int = 50


class PackageRejected(Exception):
    """The source package is not an acceptable document.

    The category is a fixed, content-safe failure category.
    """

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def detect_format(path: Path) -> str:
    """Identify the container structurally from its magic bytes.

    A malformed or unsupported container is rejected with a fixed category;
    the filename and declared MIME type are never consulted.
    """
    with path.open("rb") as source:
        header = source.read(_HEADER_WINDOW)
    if header.startswith(_ZIP_MAGIC):
        return DOCX
    if header.startswith(_OLE_MAGIC):
        # An OLE compound container is either a legacy binary document or an
        # encrypted OOXML package. Both are unsupported without inspection.
        raise PackageRejected(UNSUPPORTED_FORMAT)
    if _PDF_MAGIC in header:
        return PDF
    raise PackageRejected(MALFORMED_DOCUMENT)


def preflight_for_format(path: Path, format_name: str, limits: DocumentLimits) -> None:
    if format_name == PDF:
        preflight_pdf(path, limits)
    else:
        preflight_docx(path, limits)


def preflight_pdf(path: Path, limits: DocumentLimits) -> None:
    size = path.stat().st_size
    if size > limits.max_bytes:
        raise PackageRejected(OVER_BUDGET)

    with path.open("rb") as source:
        header = source.read(_HEADER_WINDOW)
        source.seek(max(0, size - _PDF_TRAILER_WINDOW))
        trailer = source.read(_PDF_TRAILER_WINDOW)

    if _PDF_MAGIC not in header:
        raise PackageRejected(MALFORMED_DOCUMENT)
    # A complete PDF ends with an EOF marker; a missing one means truncated or
    # partial bytes, which are never repaired.
    if _PDF_EOF_MARKER not in trailer:
        raise PackageRejected(MALFORMED_DOCUMENT)
    # The encryption dictionary lives in the trailer. An encrypted PDF cannot
    # be read without decryption, and ApplyKit never decrypts.
    if _PDF_ENCRYPT_MARKER in trailer:
        raise PackageRejected(UNSUPPORTED_FORMAT)

    # Counting pages requires reading the page tree. The file is already
    # bounded by the upload size, and every parse failure stays content-safe.
    try:
        reader = PdfReader(str(path), strict=True)
    except Exception:  # noqa: BLE001 - untrusted input must fail safe
        raise PackageRejected(MALFORMED_DOCUMENT) from None
    if reader.is_encrypted:
        raise PackageRejected(UNSUPPORTED_FORMAT)
    try:
        page_count = len(reader.pages)
    except Exception:  # noqa: BLE001 - untrusted input must fail safe
        raise PackageRejected(MALFORMED_DOCUMENT) from None
    if page_count == 0:
        raise PackageRejected(MALFORMED_DOCUMENT)
    if page_count > limits.max_pdf_pages:
        raise PackageRejected(OVER_BUDGET)


def preflight_docx(path: Path, limits: DocumentLimits) -> None:
    if path.stat().st_size > limits.max_bytes:
        raise PackageRejected(OVER_BUDGET)

    with path.open("rb") as source:
        magic = source.read(8)
    if magic.startswith(_OLE_MAGIC):
        # An OLE compound container is either a legacy binary document or an
        # encrypted OOXML package. Both are unsupported without inspection.
        raise PackageRejected(UNSUPPORTED_FORMAT)

    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise PackageRejected(MALFORMED_DOCUMENT) from None

    with archive:
        _preflight_members(archive, limits)
        _preflight_content_type(archive)


def _preflight_members(archive: zipfile.ZipFile, limits: DocumentLimits) -> None:
    members = archive.infolist()
    if len(members) > limits.max_members:
        raise PackageRejected(OVER_BUDGET)

    expanded = 0
    for member in members:
        name = member.filename
        normalised = name.replace("\\", "/")
        if normalised.startswith("/") or normalised.startswith("~"):
            raise PackageRejected(MALFORMED_DOCUMENT)
        parts = normalised.split("/")
        if ".." in parts or "" in parts[:-1]:
            raise PackageRejected(MALFORMED_DOCUMENT)
        if name.lower() in _ENCRYPTED_MEMBERS:
            raise PackageRejected(UNSUPPORTED_FORMAT)
        expanded += member.file_size
        if member.file_size > limits.max_member_bytes:
            raise PackageRejected(OVER_BUDGET)
    if expanded > limits.max_expanded_bytes:
        raise PackageRejected(OVER_BUDGET)


def _preflight_content_type(archive: zipfile.ZipFile) -> None:
    if _DOCUMENT_PART not in archive.namelist():
        raise PackageRejected(MALFORMED_DOCUMENT)
    try:
        content_types = archive.read(_CONTENT_TYPES_PART)
    except zipfile.BadZipFile, OSError:
        raise PackageRejected(MALFORMED_DOCUMENT) from None

    declared = _declared_content_type(content_types)
    if declared is None:
        raise PackageRejected(MALFORMED_DOCUMENT)
    if declared != _WORDPROCESSING_MAIN:
        raise PackageRejected(UNSUPPORTED_FORMAT)


def _declared_content_type(content_types: bytes) -> str | None:
    try:
        root = ET.fromstring(content_types)
    except ET.ParseError:
        raise PackageRejected(MALFORMED_DOCUMENT) from None

    override_tag = f"{{{_CONTENT_TYPES_NAMESPACE}}}Override"
    default_tag = f"{{{_CONTENT_TYPES_NAMESPACE}}}Default"
    for element in root.iter(override_tag):
        if element.get("PartName") == f"/{_DOCUMENT_PART}":
            return element.get("ContentType")
    for element in root.iter(default_tag):
        if element.get("Extension", "").lower() == "xml":
            return element.get("ContentType")
    return None
