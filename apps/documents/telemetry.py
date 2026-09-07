"""Content-free operational and security telemetry for document extraction.

Every field must be a fixed identifier or a bounded count. Source content,
filenames, paths, hashes, extracted values, and raw exceptions never enter
telemetry. A failed cleanup marks the instance unhealthy so the platform can
recycle it.
"""

import logging
import threading
from typing import Any

__all__ = [
    "EXTRACTOR_VERSION",
    "SECURITY_LOGGER_NAME",
    "is_instance_healthy",
    "log_event",
    "mark_instance_unhealthy",
    "security_event",
]

SECURITY_LOGGER_NAME = "applykit.security"
OPERATIONAL_LOGGER_NAME = "applykit.documents"

EXTRACTOR_VERSION = 1

_unhealthy = threading.Event()


def log_event(event: str, **fields: Any) -> None:
    logging.getLogger(OPERATIONAL_LOGGER_NAME).info(event, extra={"event": event, **fields})


def security_event(event: str, **fields: Any) -> None:
    logging.getLogger(SECURITY_LOGGER_NAME).warning(event, extra={"event": event, **fields})


def mark_instance_unhealthy(reason_category: str) -> None:
    _unhealthy.set()
    security_event("instance_marked_unhealthy", reason_category=reason_category)


def is_instance_healthy() -> bool:
    return not _unhealthy.is_set()
