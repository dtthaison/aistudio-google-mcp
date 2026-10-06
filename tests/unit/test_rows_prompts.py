import pytest

from aistudio._rpc.params.prompts import list_prompts
from aistudio._rpc.rows.prompts import decode_prompt_list
from aistudio._types.prompts import PromptSummary
from aistudio.exceptions import SchemaDriftError


def test_list_prompts_request_matches_capture(captured):
    assert list_prompts() == captured("ListPrompts").request
    assert list_prompts(25) == [25]


def test_decodes_captured_library(captured):
    prompts = decode_prompt_list(captured("ListPrompts", 4).response)
    assert len(prompts) == 12
    assert prompts[0] == PromptSummary(
        id="prompt_391", title="Generating A Cute House.", type="chat", description=None,
        modified="2026-10-06T05:10:02Z", has_images=False, parent_app_id=None,
    )
    by_id = {p.id: p for p in prompts}
    assert by_id["prompt_5"].description == (
        ":::::::: Code Assistant history - Saved from AI Studio ::::::::"
    )
    assert by_id["prompt_5"].parent_app_id == "prompt_6"
    assert by_id["prompt_5"].modified == "2026-01-09T17:02:46Z"
    assert by_id["prompt_7"].type == "video"
    assert by_id["prompt_7"].modified == "2025-10-03T13:15:21Z"
    assert sum(p.type == "video" for p in prompts) == 7


def test_empty_library_decodes_to_no_prompts():
    assert decode_prompt_list([]) == []
    assert decode_prompt_list(None) == []


def test_unknown_prompt_type_and_sparse_metadata():
    meta = [None] * 12
    meta[11] = [["promptType", "STRUCTURED_PROMPT"], ["hasImages", "true"], "junk"]
    sparse, bare = decode_prompt_list([[["prompts/a", None, None, None, meta], ["prompts/b"]]])
    assert (sparse.type, sparse.has_images, sparse.title, sparse.modified) == (
        "structured_prompt", True, None, None,
    )
    assert (bare.type, bare.has_images) == ("unknown", None)


@pytest.mark.parametrize("response", [["oops"], [["oops"]], [[[None]]]])
def test_missing_names_raise_schema_drift(response):
    with pytest.raises(SchemaDriftError):
        decode_prompt_list(response)
