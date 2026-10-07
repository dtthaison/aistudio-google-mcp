"""Never-lose file writing for generated media (shared by AI Studio and Flow)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from aistudio.exceptions import ValidationError


def check_folder(folder: Path) -> None:
    if not folder.is_dir() or not os.access(folder, os.W_OK):
        raise ValidationError(f"{folder} does not exist or is not writable",
                              hint="create the folder first; nothing was generated")


def free_name(path: Path, *, start: int | None = None) -> Path:
    """``path`` itself (unless ``start`` is given), else ``stem-N`` with the first free N."""
    if start is None and not path.exists():
        return path
    n = start or 2
    while True:
        candidate = path.with_name(f"{path.stem}-{n}{path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def write_file(path: Path, data: bytes, *, overwrite: bool) -> Path:
    """Save ``data`` and return where it landed: a paid result is never dropped.

    Without --force nothing is clobbered. If a file appeared at ``path`` meanwhile (or a dangling
    symlink holds the name), the data goes to ``stem-N`` instead. If the folder cannot take the
    data at all, it is rescued into the system temp folder, which the error names.
    """
    try:
        if overwrite:
            _place(path, data, replace=True)
            return path
        return _save_new(path, data)
    except OSError as exc:
        rescued = _rescue(path.name, data)
        raise ValidationError(
            f"could not write {path}: {exc.strerror or exc}",
            hint=f"the result was saved to {rescued}" if rescued
            else "the result could not be saved anywhere",
        ) from exc


def _save_new(path: Path, data: bytes) -> Path:
    for n in range(1, 1000):
        target = path if n == 1 else path.with_name(f"{path.stem}-{n}{path.suffix}")
        try:
            _place(target, data, replace=False)
            return target
        except FileExistsError:
            continue
    raise FileExistsError(f"no free file name next to {path}")


def _place(target: Path, data: bytes, *, replace: bool) -> None:
    """Write via a temp file; without ``replace`` a taken name raises FileExistsError."""
    folder = target.parent if str(target.parent) else Path(".")
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        if replace:
            os.replace(tmp, target)
            return
        try:
            os.link(tmp, target)  # atomic, and fails if the name is taken
        except FileExistsError:
            raise
        except OSError:  # no hard links here (exFAT, SMB, cloud-synced folders)
            with open(target, "xb") as fh:  # O_EXCL: still never clobbers
                fh.write(data)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _rescue(name: str, data: bytes) -> Path | None:
    try:
        return _save_new(Path(tempfile.gettempdir()) / f"aistudio-rescued-{name}", data)
    except OSError:
        return None
