"""Django system checks for the document extraction boundary.

Production must run with Linux process isolation and dedicated non-persistent
temporary storage; a missing requirement is an error. Development without the
Linux isolation path is allowed but reported clearly as reduced guarantees.
"""

import sys
from collections.abc import Sequence
from typing import Any

from django.core.checks import Error, Tags, Warning, register

from apps.documents.conf import current_settings

__all__ = ["check_document_isolation"]

_LINUX_REQUIRED = "documents.E001"
_REDUCED_GUARANTEES = "documents.W001"


@register(Tags.security)
def check_document_isolation(
    app_configs: Sequence[Any] | None = None, **kwargs: Any
) -> list[Error | Warning]:
    settings_ = current_settings()
    on_linux = sys.platform == "linux"
    if settings_.require_linux_isolation and not on_linux:
        return [
            Error(
                "Document extraction requires Linux process isolation.",
                hint=(
                    "Run production on a Linux host or container. Document imports stay "
                    "unavailable until the isolation path is restored."
                ),
                id=_LINUX_REQUIRED,
            )
        ]
    if not settings_.require_linux_isolation and not on_linux:
        return [
            Warning(
                "Document extraction is running with reduced isolation guarantees.",
                hint=(
                    "Peak memory and some process limits are not enforced outside Linux. "
                    'Set DOCUMENT_EXTRACTION["REQUIRE_LINUX_ISOLATION"] in production.'
                ),
                id=_REDUCED_GUARANTEES,
            )
        ]
    return []
