"""Capture fixture files: ``<out>/<Method>/<n>.json`` (spec §8.1)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from aistudio.exceptions import ValidationError


def rpc_method_name(url: str) -> str:
    parsed = urlparse(url)
    if "/$rpc/" in parsed.path:
        return parsed.path.rsplit("/", 1)[-1]
    # Only host + first path segment: later segments can hold IDs or emails.
    first = parsed.path.strip("/").split("/", 1)[0]
    slug = re.sub(r"[^A-Za-z0-9]+", "_", f"{parsed.hostname}_{first}").strip("_")
    return f"other_{slug}"[:120]


class FixtureWriter:
    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self._next: dict[str, int] = {}

    def write(self, record: dict[str, Any]) -> Path:
        method = record["method"]
        directory = self.out_dir / method
        directory.mkdir(parents=True, exist_ok=True)
        if method not in self._next:
            existing = [int(p.stem) for p in directory.glob("*.json") if p.stem.isdigit()]
            self._next[method] = max(existing, default=0) + 1
        path = directory / f"{self._next[method]}.json"
        self._next[method] += 1
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return path


def load_fixture(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "url" not in data or "request" not in data:
        raise ValidationError(f"{path} is not a capture fixture")
    return data


def iter_fixtures(root: Path) -> list[Path]:
    def key(p: Path) -> tuple[str, int]:
        return p.parent.name, int(p.stem) if p.stem.isdigit() else 0

    return sorted(root.glob("*/*.json"), key=key)
