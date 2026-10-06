from datetime import timezone

import pytest

from aistudio._rpc import wire
from aistudio._rpc.params.models import list_models
from aistudio._rpc.rows import as_int, as_str
from aistudio._rpc.rows.models import decode_models
from aistudio._types.models import Model, PricingRow
from aistudio.exceptions import SchemaDriftError


def catalog(captured):
    return {m.id: m for m in decode_models(captured("ListModels").response, tz=timezone.utc)}


def test_list_models_request_matches_capture(captured):
    assert list_models() == captured("ListModels").request


def test_decodes_every_captured_model_with_known_categories(captured):
    models = decode_models(captured("ListModels").response, tz=timezone.utc)
    assert len(models) == 42
    assert all(m.categories for m in models)
    assert not [c for m in models for c in m.categories if c.startswith("code:")]


def test_lyria_matches_the_picker_card(captured):
    m = catalog(captured)["lyria-3.5"]
    assert m.display_name == "Lyria 3.5"
    assert m.categories == ("audio", "music")
    assert m.command == "generate music"
    assert m.pricing == (PricingRow("output", "Song generation (*Output per song)", 0.08),)
    assert m.release_date == "2026-09-03"
    assert m.knowledge_cutoff == "2025-01"
    assert m.description.startswith("Our advanced, full-song generative model")
    assert m.docs_url == "https://aistudio.google.com/docs/music-generation"
    assert m.methods == ("generateContent", "countTokens")
    assert (m.input_token_limit, m.output_token_limit) == (1048576, 65536)


def test_per_modality_pricing_rows(captured):
    m = catalog(captured)["gemini-3.1-flash-lite"]
    assert m.categories == ("featured", "gemini")
    assert m.command == "ask"
    assert m.pricing == (
        PricingRow("input", "Text, image and video", 0.25),
        PricingRow("input", "Audio", 0.5),
        PricingRow("output", "Text, image and video", 1.5),
        PricingRow("output", "Audio", 1.5),
    )
    assert m.release_date == "2026-05-07"


def test_summary_pricing(captured):
    m = catalog(captured)["gemini-3.8-flash"]
    assert m.pricing == ()
    assert m.pricing_summary == (
        "All context lengths * input: $0.75 / output: $3.75 through December 31, 2026"
    )
    assert m.pricing_url == "https://aistudio.google.com/docs/pricing#gemini-3.8-flash"
    assert m.knowledge_cutoff is None


@pytest.mark.parametrize(
    "model_id,categories,command",
    [
        ("veo-3.1-generate-preview", ("video",), "generate video"),
        ("antigravity-preview-09-2026", ("agents",), "agent run"),
        ("gemma-4-31b-it", ("gemma",), "ask"),
        ("gemini-3.5-transcribe", ("featured", "gemini", "audio"), "transcribe"),
        ("gemini-3.5-transcribe-live", ("featured", "audio", "live"), "live"),
        ("gemini-3.8-flash-tts", ("featured", "audio"), "generate speech"),
        ("gemini-3.1-flash-image", ("featured", "gemini", "images"), "generate image"),
    ],
)
def test_categories_map_to_commands(captured, model_id, categories, command):
    m = catalog(captured)[model_id]
    assert (m.categories, m.command) == (categories, command)


def test_sparse_and_malformed_rows_decode_with_nulls():
    card = [None] * 21
    card[0], card[19], card[20] = "not-a-pricing-block", ["not-a-number"], "x"
    odd = [None] * 75
    odd[0], odd[3], odd[5], odd[7], odd[38], odd[74] = (
        "models/odd", "Odd", "big", "not-a-list", card, [999],
    )
    plain, weird = decode_models([[["models/plain"], odd]], tz=timezone.utc)
    assert plain == Model(id="plain", display_name=None, description=None, categories=(),
                          command=None)
    assert weird.display_name == "Odd"
    assert weird.input_token_limit is None
    assert weird.methods == ()
    assert weird.pricing == ()
    assert weird.release_date is None and weird.knowledge_cutoff is None
    assert weird.categories == ("code:999",)
    assert weird.command is None and weird.unsupported_category


@pytest.mark.parametrize("response", [[], [["oops"]], [[[None]]], [[[42]]]])
def test_missing_required_fields_raise_schema_drift(response):
    with pytest.raises(SchemaDriftError):
        decode_models(response)


def test_to_dict_is_json_ready(captured):
    data = catalog(captured)["lyria-3.5"].to_dict()
    assert data["categories"] == ["audio", "music"]
    assert data["pricing"] == [
        {"direction": "output", "label": "Song generation (*Output per song)", "price_usd": 0.08}
    ]
    assert data["unsupported_category"] is False


def test_opt_index_and_coercions():
    assert wire.opt_index([1, [2, 3]], 1, 0) == 2
    assert wire.opt_index([1], 5) is None
    assert wire.opt_index("x", 0) is None
    assert (as_str("a"), as_str(1)) == ("a", None)
    assert (as_int(3), as_int(True), as_int("3")) == (3, None, None)
