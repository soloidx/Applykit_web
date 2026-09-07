"""Structural intake validation for DOCX uploads.

The source document is never trusted by filename or declared MIME type. The
package itself is identified structurally and preflighted against configured
member and expansion limits before any parsing work is attempted.
"""

import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

from apps.documents.protocol import MALFORMED_DOCUMENT, OVER_BUDGET, UNSUPPORTED_FORMAT

__all__ = ["DocumentLimits", "PackageRejected", "preflight_docx"]

_OLE_MAGIC = b"\xd0\xcf\x11\xe0"
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


class PackageRejected(Exception):
    """The source package is not an acceptable DOCX document.

    The category is a fixed, content-safe failure category.
    """

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


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
