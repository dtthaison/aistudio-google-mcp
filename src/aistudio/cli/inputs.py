"""Read a command's text from an argument or a file, with clear errors for bad files."""

from __future__ import annotations

from pathlib import Path

from aistudio.exceptions import ValidationError

MAX_TEXT_BYTES = 1024 * 1024


def read_text(text: str | None, text_file: Path | None, *, arg: str, option: str,
              max_bytes: int | None = MAX_TEXT_BYTES) -> str:
    """Exactly one of ``text`` and ``text_file``; a file must be UTF-8 (and at most
    ``max_bytes``, when given)."""
    if (text is None) == (text_file is None):
        raise ValidationError(f"give either {arg} or {option}", hint="not both, not neither")
    if text_file is None:
        return text or ""
    try:
        if max_bytes is not None and text_file.stat().st_size > max_bytes:
            raise ValidationError(f"{text_file} is larger than {max_bytes // 1024 // 1024} MB",
                                  hint="pass a shorter text file")
        return text_file.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError(f"{text_file} is not UTF-8 text",
                              hint="save the file as UTF-8 text") from exc
    except OSError as exc:
        raise ValidationError(f"cannot read {text_file}: {exc.strerror or exc}") from exc
