from collections.abc import Callable
from pathlib import Path

import pytest
from docx_support import build_docx


@pytest.fixture
def make_docx(tmp_path: Path) -> Callable[..., Path]:
    def factory(**kwargs: object) -> Path:
        return build_docx(tmp_path / "source.docx", **kwargs)  # type: ignore[arg-type]

    return factory
