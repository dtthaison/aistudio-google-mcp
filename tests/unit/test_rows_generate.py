import pytest

from aistudio._rpc.params.generate import count_tokens, text_contents
from aistudio._rpc.rows.generate import decode_token_count
from aistudio._types.chat import TokenCount
from aistudio.exceptions import SchemaDriftError


def test_count_tokens_request_matches_capture(captured):
    assert count_tokens("gemini-3.1-flash-lite-image", "generate a cute house") == (
        captured("CountTokens", 3).request
    )


def test_text_contents_shape_matches_generate_content_capture(captured):
    generate = captured("GenerateContent", 2).request
    assert text_contents("generate a cute house") == generate[1]


@pytest.mark.parametrize("n,total", [(1, 4), (3, 5)])
def test_decodes_captured_counts(captured, n, total):
    count = decode_token_count("m", captured("CountTokens", n).response)
    assert count == TokenCount("m", total, {"TEXT": total})


def test_zero_is_omitted_on_the_wire():
    assert decode_token_count("m", []) == TokenCount("m", 0, {})
    assert decode_token_count("m", [None, [], None, None, None, [[9, 2], "junk"]]) == (
        TokenCount("m", 0, {"MODALITY_9": 2})
    )


def test_non_integer_total_is_schema_drift():
    with pytest.raises(SchemaDriftError):
        decode_token_count("m", ["five"])
