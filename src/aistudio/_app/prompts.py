"""Prompt library use cases (spec §6.3)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from aistudio._app.apps import pick
from aistudio._media.save import write_file
from aistudio.client import AIStudioClient
from aistudio.exceptions import NotFoundError, ValidationError

TYPE_SCAN = 1000  # page size accepted live (2026-10-09); `app list` asks for 1000 too

_LIST_HINT = 'see "aistudio prompt list"'


async def list_prompts(
    client: AIStudioClient, *, limit: int = 100, prompt_type: str | None = None
) -> dict[str, Any]:
    # A type filter looks through up to 1000 prompts, then keeps `limit` of them
    prompts = await client.prompts.list(TYPE_SCAN if prompt_type else limit)
    if prompt_type:
        prompts = [p for p in prompts if p.type == prompt_type][:limit]
    return {"prompts": [p.to_dict() for p in prompts], "count": len(prompts)}


async def get_prompt(client: AIStudioClient, wanted: str) -> dict[str, Any]:
    """One prompt in full, from an id, a ``prompts/<id>`` name or a unique prefix of an id."""
    wanted = wanted.strip().removeprefix("prompts/")
    summaries = await client.prompts.list(TYPE_SCAN)
    try:
        prompt_id = pick(summaries, lambda p: p.id, wanted, noun="prompt", hint=_LIST_HINT).id
    except NotFoundError as not_listed:
        # Past the 1000 newest prompts the listing cannot show it; a full id still reads
        try:
            return (await client.prompts.get(wanted)).to_dict()
        except NotFoundError:
            raise not_listed from None
    return (await client.prompts.get(prompt_id)).to_dict()


def _num(value: Any) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)


def describe(data: dict[str, Any]) -> list[tuple[str, str]]:
    """The labelled facts about a prompt (model, generation settings), skipping unset ones."""
    s = data["settings"]
    rows: list[tuple[str, str]] = [("Type", data["type"])]
    if data["model"]:
        rows.append(("Model", data["model"]))
    for label, key in (("Temperature", "temperature"), ("Top-p", "top_p"), ("Top-k", "top_k"),
                       ("Max output tokens", "max_output_tokens"),
                       ("Aspect ratio", "aspect_ratio"), ("Duration (s)", "duration_seconds")):
        if s.get(key) is not None:
            rows.append((label, _num(s[key])))
    if s.get("response_modalities"):
        rows.append(("Response modalities", ", ".join(s["response_modalities"])))
    if s.get("safety_settings"):
        rows.append(("Safety", ", ".join(f"{x['category']}={x['threshold']}"
                                         for x in s["safety_settings"])))
    if data["modified"]:
        rows.append(("Modified", data["modified"]))
    return rows


def attachment_label(att: dict[str, Any]) -> str:
    if att["source"] == "drive":
        return f"Drive file {att['id']}"
    return att["mime"] or "attachment"


def to_markdown(data: dict[str, Any], *, thoughts: bool = False) -> str:
    out = [f"# {data['title'] or '(untitled)'}", ""]
    out += [f"- {label}: {value}" for label, value in describe(data)]
    if data["description"]:
        out += ["", data["description"]]
    if data["system_instruction"]:
        out += ["", "## System instructions", "", data["system_instruction"]]
    for turn in data["turns"]:
        out += ["", f"## {turn['role'].capitalize()}"]
        for att in turn["attachments"]:
            out += ["", f"_Attachment: {attachment_label(att)}_"]
        if thoughts and turn["thoughts"]:
            out += ["", "### Thinking", "", turn["thoughts"]]
        if turn["text"]:
            out += ["", turn["text"]]
    return "\n".join(out) + "\n"


def save_markdown(data: dict[str, Any], path: Path, *, force: bool, thoughts: bool = False
                  ) -> Path:
    """Write the prompt as markdown and return where it landed; never replaces without force."""
    if os.path.lexists(path) and not force:
        raise ValidationError(f"{path} already exists", hint="pass --force to overwrite it")
    folder = path.parent if str(path.parent) else Path(".")
    if not folder.is_dir():
        raise ValidationError(f"{folder} is not a folder", hint="create it first")
    return write_file(path, to_markdown(data, thoughts=thoughts).encode(), overwrite=force)
