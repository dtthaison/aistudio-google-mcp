import base64

import pytest

from aistudio._rpc.rows.media import decode_media, decode_text, extension_for, finish_reason
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



def text_response(*texts, finish=1):
    return [[chunk([[None, t]]) for t in texts] + [chunk([[None, ""]], finish)]]


def test_decode_text_reads_the_reply():
    # Plan I: a transcription reply is text parts, possibly over several chunks.
    assert decode_text(text_response("Hello ", "world.")) == "Hello world."
    with pytest.raises(SchemaDriftError):
        decode_text(text_response())
    with pytest.raises(ServerError):
        decode_text(text_response("partial", finish=3))  # stopped early (e.g. safety)


def test_decode_text_reads_a_transcript_part():
    # Live: the transcribe model puts its text in slot 22 of the part, not slot 1.
    part = [None] * 22 + [["Xin chào, đây là cửa hàng Yuri Handmade."]]
    response = [[chunk([part]), chunk([[None, ""]], 1)]]
    assert decode_text(response) == "Xin chào, đây là cửa hàng Yuri Handmade."
