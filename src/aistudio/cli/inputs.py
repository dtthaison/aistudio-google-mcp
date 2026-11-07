"""Read a command's text from an argument or a file, with clear errors for bad files."""

from __future__ import annotations

import json
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
        return text_file.read_text(encoding="utf-8-sig")  # a BOM is not text
    except UnicodeDecodeError as exc:
        raise ValidationError(f"{text_file} is not UTF-8 text",
                              hint="save the file as UTF-8 text") from exc
    except OSError as exc:
        raise ValidationError(f"cannot read {text_file}: {exc.strerror or exc}") from exc


def read_prompts(path: Path) -> list[str]:
    """The prompts in a JSON Lines file: one JSON string, or one ``{"prompt": "..."}`` object,
    per line. Newlines inside a prompt are written ``\\n`` in the JSON, so a prompt of many
    lines (even with blank lines) stays one entry. Blank lines are skipped."""
    prompts: list[str] = []
    text = read_text(None, path, arg="PROMPT", option="--prompts-file")
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        where = f"{path} line {number}"
        try:
            entry = json.loads(line)
        except ValueError as exc:
            raise ValidationError(f"{where} is not JSON: {exc}",
                                  hint='write each prompt as a JSON string, e.g. "a red apple" '
                                       '(JSON Lines, one per line)') from exc
        if isinstance(entry, dict):
            extra = sorted(set(entry) - {"prompt"})
            if extra or "prompt" not in entry:
                raise ValidationError(
                    f'{where}: an object holds only a "prompt" key'
                    + (f", not {', '.join(extra)}" if extra else ""),
                    hint="options such as the model apply to the whole batch: pass them as flags")
            entry = entry["prompt"]
        if not isinstance(entry, str) or not entry.strip():
            raise ValidationError(f"{where} is not a prompt: expected a non-empty JSON string",
                                  hint='e.g. "a red apple" or {"prompt": "a red apple"}')
        prompts.append(entry.strip())
    if not prompts:
        raise ValidationError(f"{path} has no prompts", hint="write one JSON string per line")
    return prompts
