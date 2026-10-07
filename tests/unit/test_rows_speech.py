import pytest

from aistudio._rpc.rows.speech import decode_speech
from aistudio.exceptions import SchemaDriftError, ServerError


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


def finish(reason=1):
    return [[[[[[None, ""]], "model"], reason]]]


def test_chunks_are_joined_in_order():
    audio = decode_speech([[chunk("AQA="), chunk("AgA="), chunk("AwA="), finish()]])
    assert audio.pcm == b"\x01\x00\x02\x00\x03\x00"
    assert (audio.rate, audio.channels) == (8000, 1)


@pytest.mark.parametrize("data", ["", "AQ=="])  # empty, and one odd byte
def test_empty_or_odd_length_audio_is_schema_drift(data):
    with pytest.raises(SchemaDriftError):
        decode_speech([[chunk(data), finish()]])


def test_a_missing_finish_reason_is_schema_drift():
    with pytest.raises(SchemaDriftError):
        decode_speech([[chunk("AQA=")]])


def test_a_stream_that_stopped_early_is_a_server_error():
    with pytest.raises(ServerError) as info:
        decode_speech([[chunk("AQA="), finish(3)]])
    assert "stopped early" in info.value.message
