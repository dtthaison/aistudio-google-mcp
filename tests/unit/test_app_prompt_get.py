import httpx
import pytest
import respx

from aistudio._app.prompts import get_prompt, save_markdown, to_markdown
from aistudio._rpc import wire
from aistudio._transport.http import method_url
from aistudio.exceptions import NotFoundError, ValidationError


def serve_list(captured):
    return respx.post(method_url("ListPrompts")).mock(
        return_value=httpx.Response(200, text=captured("ListPrompts", 4).raw["response"]["body"])
    )


def serve_get(body):
    return respx.post(method_url("GetPrompt")).mock(
        return_value=httpx.Response(200, text=wire.encode(body))
    )


@respx.mock
async def test_get_resolves_a_prefix_then_reads_the_full_prompt(make_client, captured,
                                                                prompt_wire):
    w = prompt_wire
    serve_list(captured)
    route = serve_get(w.prompt([w.chunk("Hi")], name="prompts/prompt_391"))
    async with make_client() as client:
        data = await get_prompt(client, "prompt_39")
    assert route.calls.last.request.content == b'["prompts/prompt_391"]'
    assert data["id"] == "prompt_391" and data["turns"][0]["text"] == "Hi"


@respx.mock
async def test_get_accepts_the_prompts_prefix(make_client, captured, prompt_wire):
    serve_list(captured)
    route = serve_get(prompt_wire.prompt([], name="prompts/prompt_391"))
    async with make_client() as client:
        await get_prompt(client, "prompts/prompt_391")
    assert route.calls.last.request.content == b'["prompts/prompt_391"]'


@respx.mock
async def test_an_ambiguous_prefix_lists_the_matches_and_reads_nothing(make_client, captured):
    serve_list(captured)  # no GetPrompt route: reading anything would fail as "not mocked"
    async with make_client() as client:
        with pytest.raises(ValidationError) as info:
            await get_prompt(client, "prompt_")
    assert "matches several prompts" in info.value.message


@respx.mock
async def test_an_id_outside_the_listing_is_tried_directly(make_client, captured, prompt_wire):
    serve_list(captured)
    route = serve_get(prompt_wire.prompt([], name="prompts/old-prompt-id"))
    async with make_client() as client:
        data = await get_prompt(client, "old-prompt-id")
    assert route.calls.last.request.content == b'["prompts/old-prompt-id"]'
    assert data["id"] == "old-prompt-id"


@respx.mock
async def test_unknown_prompt_is_not_found(make_client, captured):
    serve_list(captured)
    respx.post(method_url("GetPrompt")).mock(return_value=httpx.Response(
        404, text=wire.encode([None, [5, "Requested entity was not found."]])))
    async with make_client() as client:
        with pytest.raises(NotFoundError) as info:
            await get_prompt(client, "zzz")
    assert info.value.hint == 'see "aistudio prompt list"'


def _data(w, **kw):
    return {"id": "x", "title": "Plan", "description": None, "type": "chat", "model": "m",
            "system_instruction": "Be brief.", "modified": "2026-01-01T00:00:00Z",
            "parent_app_id": None,
            "settings": {"temperature": 0.5, "top_p": None, "top_k": 64,
                         "max_output_tokens": 100, "safety_settings": [],
                         "response_modalities": []},
            "turns": [
                {"role": "user", "text": "Q1", "thoughts": None, "at": None,
                 "attachments": [{"source": "inline", "id": None, "mime": "image/png"}]},
                {"role": "model", "text": "A1", "thoughts": "thinking...", "at": None,
                 "attachments": []}],
            **kw}


def test_markdown_has_every_section_and_full_text(prompt_wire):
    md = to_markdown(_data(prompt_wire))
    assert md.startswith("# Plan\n")
    assert "- Model: m" in md and "- Temperature: 0.5" in md and "- Top-k: 64" in md
    assert "## System instructions\n\nBe brief." in md
    assert "## User\n\n_Attachment: image/png_\n\nQ1" in md
    assert "## Model\n\nA1" in md
    assert "thinking..." not in md


def test_markdown_can_include_thoughts(prompt_wire):
    md = to_markdown(_data(prompt_wire), thoughts=True)
    assert "### Thinking\n\nthinking..." in md


def test_save_markdown_refuses_to_overwrite_without_force(tmp_path, prompt_wire):
    target = tmp_path / "p.md"
    target.write_text("keep me")
    with pytest.raises(ValidationError) as info:
        save_markdown(_data(prompt_wire), target, force=False)
    assert "--force" in (info.value.hint or "") and target.read_text() == "keep me"
    assert save_markdown(_data(prompt_wire), target, force=True) == target
    assert target.read_text().startswith("# Plan")


def test_save_markdown_needs_an_existing_folder(tmp_path, prompt_wire):
    with pytest.raises(ValidationError):
        save_markdown(_data(prompt_wire), tmp_path / "missing" / "p.md", force=False)
