import pytest

from aistudio._rpc.params.prompts import get_prompt
from aistudio._rpc.rows.prompts import decode_prompt
from aistudio.exceptions import SchemaDriftError


def test_get_prompt_request_names_the_prompt():
    assert get_prompt("abc") == ["prompts/abc"]


def test_decodes_a_chat_with_settings_and_system_instructions(prompt_wire):
    w = prompt_wire
    body = w.prompt(
        [w.chunk("Hello there", at=1778571110), w.chunk("Hi!", role="model", at=1778571120)],
        system="Be brief.", run=w.settings(temperature=0.5, top_p=0.9, top_k=40, max_tokens=1024),
    )
    p = decode_prompt(body)
    assert (p.id, p.title, p.type) == ("synthetic-id-1", "A synthetic chat", "chat")
    assert p.model == "gemini-test"
    assert p.system_instruction == "Be brief."
    assert p.settings["temperature"] == 0.5
    assert (p.settings["top_p"], p.settings["top_k"], p.settings["max_output_tokens"]) == (
        0.9, 40, 1024)
    assert p.modified == "2026-05-12T07:35:44Z"
    assert [(t.role, t.text) for t in p.turns] == [("user", "Hello there"), ("model", "Hi!")]
    assert p.turns[0].at == "2026-05-12T07:31:50Z"


def test_empty_system_instruction_and_unset_settings_are_none(prompt_wire):
    p = decode_prompt(prompt_wire.prompt([prompt_wire.chunk("x")]))
    assert p.system_instruction is None
    assert p.settings["temperature"] is None
    assert p.settings["safety_settings"] == [] and p.settings["response_modalities"] == []


def test_safety_settings_and_modalities_get_names(prompt_wire):
    w = prompt_wire
    run = w.settings(safety=[[None, None, 7, 5], [None, None, 10, 2], [None, None, 99, 7]],
                     modalities=[2, 1])
    p = decode_prompt(w.prompt([], run=run))
    assert p.settings["safety_settings"] == [
        {"category": "harassment", "threshold": "off"},
        {"category": "dangerous_content", "threshold": "block_medium_and_above"},
        {"category": "category_99", "threshold": "threshold_7"},
    ]
    assert p.settings["response_modalities"] == ["image", "text"]


def test_turn_merges_attachments_and_text_and_splits_thoughts(prompt_wire):
    w = prompt_wire
    p = decode_prompt(w.prompt([
        w.chunk(None, drive="drive-file-id"),
        w.chunk(None, inline="image/png"),
        w.chunk("Describe these"),
        w.chunk("Planning the answer", role="model", thought=True),
        w.chunk(None, role="model", inline="image/jpeg"),
        w.chunk("Part one", role="model"),
        w.chunk("Part two", role="model"),
    ]))
    user, model = p.turns
    assert user.text == "Describe these"
    assert user.attachments == [
        {"source": "drive", "id": "drive-file-id", "mime": None},
        {"source": "inline", "id": None, "mime": "image/png"},
    ]
    assert model.thoughts == "Planning the answer"
    assert model.text == "Part one\n\nPart two"
    assert model.attachments == [{"source": "inline", "id": None, "mime": "image/jpeg"}]


def test_inline_attachment_data_is_never_kept(prompt_wire):
    p = decode_prompt(prompt_wire.prompt([prompt_wire.chunk(None, inline="image/png")]))
    assert "QUJD" not in repr(p.to_dict())


def test_empty_chunks_are_dropped(prompt_wire):
    w = prompt_wire
    assert decode_prompt(w.prompt([w.chunk(""), w.chunk(None, role="model")])).turns == []


def test_a_prompt_without_settings_or_chunks_still_decodes():
    p = decode_prompt(["prompts/x", None, None, None, ["Only a title"]])
    assert (p.title, p.model, p.turns, p.type) == ("Only a title", None, [], "unknown")


def test_decodes_a_video_prompt(prompt_wire):
    p = decode_prompt(prompt_wire.veo("A fox runs"))
    assert p.type == "video" and p.model == "veo-test"
    assert p.settings["aspect_ratio"] == "9:16" and p.settings["duration_seconds"] == 8
    assert [(t.role, t.text) for t in p.turns] == [("user", "A fox runs")]
    assert p.turns[0].attachments == [{"source": "inline", "id": None, "mime": "image/jpeg"}]


@pytest.mark.parametrize("response", [None, [], "oops", [None], [7]])
def test_a_response_without_a_name_is_schema_drift(response):
    with pytest.raises(SchemaDriftError):
        decode_prompt(response)
