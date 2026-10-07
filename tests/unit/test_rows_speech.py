import pytest

from aistudio._rpc.rows.speech import decode_speech
from aistudio.exceptions import SchemaDriftError


def test_decodes_the_captured_speech_response(captured):
    audio = decode_speech(captured("GenerateContent", 3).response)
    assert audio.mime_type == "audio/l16; rate=24000; channels=1"
    assert (audio.rate, audio.channels) == (24000, 1)
    assert len(audio.pcm) == 506880
    assert audio.seconds == pytest.approx(10.56)


def test_placeholder_media_is_schema_drift(captured):
    with pytest.raises(SchemaDriftError):
        decode_speech(captured("GenerateContent", 1).response)


@pytest.mark.parametrize(
    "response",
    [
        [],
        [[[[[[[[None, None, ["image/jpeg", "AAAA"]]]]]]]]],
        [[[[[[[[None, None, ["audio/mpeg", "AAAA"]]]]]]]]],
    ],
)
def test_missing_or_non_pcm_audio_is_schema_drift(response):
    with pytest.raises(SchemaDriftError):
        decode_speech(response)


def chunk(data):
    return [[[[[[[None, None, ["audio/l16; rate=8000; channels=1", data]]]]]]]]


def test_chunks_are_joined_in_order():
    audio = decode_speech([[chunk("AQA="), chunk("AgA="), chunk("AwA=")]])
    assert audio.pcm == b"\x01\x00\x02\x00\x03\x00"
    assert (audio.rate, audio.channels) == (8000, 1)
