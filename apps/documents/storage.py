"""Private per-request temporary storage for source documents.

Directories are generated per request on dedicated non-persistent storage,
never ordinary Django storage. The caller owns the directory lifecycle and
must remove it on every path.
"""

import shutil
import stat
import tempfile
from pathlib import Path

__all__ = ["create_private_dir", "remove_private_dir"]


def create_private_dir(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix="request-", dir=str(root)))
    path.chmod(stat.S_IRWXU)
    return path


def remove_private_dir(path: Path) -> None:
    shutil.rmtree(path)
