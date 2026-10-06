"""Atomic, owner-only JSON files guarded by a sibling lock file."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from filelock import FileLock


def lock_for(path: Path) -> FileLock:
    return FileLock(str(path.with_name(f".{path.name}.lock")))


def write_private_json(path: Path, data: Any) -> None:
    """Write ``data`` as JSON to ``path`` atomically with mode 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with lock_for(path):
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def read_json(path: Path) -> Any:
    with lock_for(path):
        return json.loads(path.read_text(encoding="utf-8"))
