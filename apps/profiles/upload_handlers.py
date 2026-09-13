"""The route-specific upload handler for Candidate Profile source documents.

The source is held in bounded memory for the duration of the request and never
touches ordinary Django storage. The extraction boundary takes it from there
and writes its own private per-request directory on non-persistent storage.
"""

from __future__ import annotations

import os
from io import UnsupportedOperation
from typing import Any

from django.core.files.uploadhandler import MemoryFileUploadHandler

from apps.documents.conf import current_settings

__all__ = ["ProfileSourceUploadHandler"]

# Multipart framing (boundaries, part headers) pushes the raw body slightly
# above the document size, so activation allows a small margin. The extraction
# boundary still enforces the exact document bound on the file itself.
_MULTIPART_SLACK_BYTES = 1024 * 1024


class ProfileSourceUploadHandler(MemoryFileUploadHandler):
    """Activate in memory up to the configured document size, not Django's default.

    ``MemoryFileUploadHandler`` decides activation from
    ``FILE_UPLOAD_MAX_MEMORY_SIZE``; this route accepts the document-extraction
    bound instead, so larger but still supported DOCX uploads reach extraction.
    """

    def handle_raw_input(
        self,
        input_data: Any,
        META: Any,
        content_length: int | None,
        boundary: Any,
        encoding: str | None = None,
    ) -> None:
        max_bytes = current_settings().max_upload_bytes + _MULTIPART_SLACK_BYTES
        stream = getattr(input_data, "_stream", input_data)
        try:
            content_length = stream.seek(0, os.SEEK_END)
        except UnsupportedOperation, AttributeError:
            pass
        else:
            stream.seek(0)
        self.activated = content_length is not None and content_length <= max_bytes
