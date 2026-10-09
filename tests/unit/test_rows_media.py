import base64

import pytest

from aistudio._rpc.rows.media import decode_media, extension_for, finish_reason
from aistudio.exceptions import SchemaDriftError, ServerError

JPEG = b"\xff\xd8\xff\xe0fakejpeg\xff\xd9"
MP3 = b"ID3\x04\x00fakemp3"


def b64(data):
    return base64.b64encode(data).decode()


def chunk(parts, finish=None):
    candidate = [[parts, "model"]] + ([finish] if finish is not None else [])
    return [[candidate], None, [9, 1, 10], None, None, None, None, "v1_id"]


def image_response(*images, finish=1):
    part = [None, None, ["image/jpeg", b64(images[0])]] + [None] * 11 + ["SIG" * 100]
    chunks = [chunk([part])]
    chunks += [chunk([[None, None, ["image/jpeg", b64(i)]]]) for i in images[1:]]
    return [chunks + [chunk([[None, ""]], finish)]]


def music_response(finish=1, text="<instrumental>"):
    return [[chunk([[None, text]]), chunk([[None, None, ["audio/mpeg", b64(MP3)]]]),
             chunk([[None, ""]], finish)]]


def test_image_parts_and_finish():
    media = decode_media(image_response(JPEG), "image")
    assert [(p.mime_type, p.data) for p in media.parts] == [("image/jpeg", JPEG)]
    assert media.text == ""


def test_several_images_are_kept_in_order():
    media = decode_media(image_response(JPEG, JPEG + b"2"), "image")
    assert [p.data for p in media.parts] == [JPEG, JPEG + b"2"]


def test_music_audio_and_text():
    media = decode_media(music_response(), "audio")
    assert [(p.mime_type, p.data) for p in media.parts] == [("audio/mpeg", MP3)]
    assert media.text == "<instrumental>"


def test_audio_parts_of_one_mime_are_joined():
    response = music_response()
    response[0].insert(2, chunk([[None, None, ["audio/mpeg", b64(b"more")]]]))
    assert decode_media(response, "audio").parts[0].data == MP3 + b"more"


def test_audio_where_an_image_was_expected_is_reported_with_its_text():
    with pytest.raises(ServerError) as info:
        decode_media(music_response(), "image")
    assert "<instrumental>" in info.value.message


def test_bad_base64_is_schema_drift():
    response = music_response()
    response[0][1][0][0][0][0][0][2][1] = "%%%"
    with pytest.raises(SchemaDriftError):
        decode_media(response, "audio")


def test_missing_finish_is_schema_drift_and_early_stop_is_server_error():
    no_finish = music_response()
    no_finish[0].pop()
    with pytest.raises(SchemaDriftError):
        decode_media(no_finish, "audio")
    with pytest.raises(ServerError) as info:
        decode_media(music_response(finish=3), "audio")
    assert "stopped early" in info.value.message


def test_finish_reason_reads_the_last_chunk():
    assert finish_reason(music_response(finish=1)) == 1


@pytest.mark.parametrize("mime,ext", [("image/jpeg", ".jpg"), ("image/png", ".png"),
                                      ("audio/mpeg", ".mp3"), ("audio/wav", ".wav"),
                                      ("application/x-what", ".bin")])
def test_extension_for(mime, ext):
    assert extension_for(mime) == ext


def text_only(text, finish):
    return [[chunk([[None, text]]), chunk([[None, ""]], finish)]]


def test_a_blocked_generation_without_media_reports_the_finish_reason():
    with pytest.raises(ServerError) as info:
        decode_media(text_only("", 3), "image")
    assert "stopped early" in info.value.message


def test_a_text_only_reply_is_shown_to_the_person():
    with pytest.raises(ServerError) as info:
        decode_media(text_only("I can't create that image.", 1), "image")
    assert "I can't create that image." in info.value.message
    assert "rephrase" in info.value.hint


def test_neither_media_nor_text_is_schema_drift():
    with pytest.raises(SchemaDriftError):
        decode_media(text_only("", 1), "audio")





LIVE_PART = [None, "Xin chào, đây là cửa hàng"] + [None] * 20 + [[
    "Xin chào, đây là cửa hàng", "spk:0",
    [["Xin", [None, 100000000], [None, 400000000]], ["chào,", [None, 400000000], [None, 900000000]],
     ["đây", ["1"], ["1", 200000000]], ["là", ["1", 200000000], ["1", 400000000]],
     ["cửa", ["1", 400000000], ["1", 800000000]], ["hàng", ["1", 800000000], ["2"]]]]]


def test_decode_transcript_reads_speakers_and_word_times():
    # Live (AI Studio defaults: word timestamps and speaker labels on).
    from aistudio._rpc.rows.media import decode_transcript

    words = [["Chúng", ["3"], ["3", 400000000]], ["len.", ["4"], ["4", 500000000]]]
    second = [None] * 22 + [["Chúng tôi bán len.", "spk:1", words]]
    transcript = decode_transcript([[chunk([LIVE_PART, second]), chunk([[None, ""]], 1)]])
    assert transcript.finish == 1
    assert transcript.text == "Xin chào, đây là cửa hàng Chúng tôi bán len."
    first, other = transcript.segments
    assert (first.speaker, first.start, first.end, first.text) == (
        "spk:0", 0.1, 2.0, "Xin chào, đây là cửa hàng")
    assert first.words[2] == ("đây", 1.0, 1.2)
    assert (other.speaker, other.start, other.end) == ("spk:1", 3.0, 4.5)


def test_decode_transcript_plain_and_cut_off():
    from aistudio._rpc.rows.media import decode_transcript

    plain = [None] * 22 + [["Just text."]]
    t = decode_transcript([[chunk([plain]), chunk([[None, ""]], 1)]])
    assert t.text == "Just text." and t.segments[0].speaker is None and t.segments[0].start is None
    cut = decode_transcript([[chunk([[None, "\n"] + [None] * 20 + [["partial"]]]),
                              chunk([[None, ""]], 2)]])
    assert (cut.text, cut.finish) == ("partial", 2)  # slot 22 wins over blank slot-1 text
    empty = decode_transcript([[chunk([[None, ""]], 1)]])
    assert empty.text == "" and empty.segments == ()


def test_decode_transcript_marks_plain_text():
    # Every live transcript came in slot 22 (with or without word times). Text only in slot 1
    # is a chat-style reply, e.g. a refusal, and must not pass silently as a transcript.
    from aistudio._rpc.rows.media import decode_transcript

    chat = decode_transcript([[chunk([[None, "I can't transcribe this."]]),
                               chunk([[None, ""]], 1)]])
    assert chat.text == "I can't transcribe this." and chat.plain is True
    live = decode_transcript([[chunk([LIVE_PART]), chunk([[None, ""]], 1)]])
    assert live.plain is False
    bare = decode_transcript([[chunk([[None] * 22 + [["Just text."]]]), chunk([[None, ""]], 1)]])
    assert bare.plain is False
