"""Flow Music's song library: the list reply and the clip record, from the shapes seen live
(2026-11-07, hand-trimmed; the ids are made up)."""

import json

import pytest

from aistudio._flow.music import (
    LIBRARY_PAGE,
    LibrarySong,
    clip_record,
    library_page,
    library_path,
    song_matches,
)
from aistudio.exceptions import SchemaDriftError, ValidationError

A = "11111111-1111-4111-8111-111111111111"
B = "22222222-2222-4222-8222-222222222222"
C = "33333333-3333-4333-8333-333333333333"
BASE = "https://storage.googleapis.com/producer-app-public/clips"


def row(clip_id=A, *, title="Morning", prompt="upbeat lofi beat with soft piano",
        lyrics="[Instrumental]", duration=None, audio=True, deleted=None, operation=None,
        created="2026-11-07T04:27:58.504133Z"):
    return {
        "id": clip_id, "op_type": "audio__create_song", "title": title, "privacy": "unlisted",
        "duration": duration or {"status": "completed", "value": "176.93866666666668"},
        "lyrics": {"status": "completed", "value": {"id": "l1", "text": lyrics}},
        "created_at": created, "deleted_at": deleted,
        "operation": operation if operation is not None else {
            "op_type": "audio__create_song", "conversation_id": B, "sound_prompt": prompt},
        "audio_url": f"{BASE}/{clip_id}.m4a" if audio else None,
        "wav_url": f"{BASE}/{clip_id}.wav" if audio else None,
    }


def listing(*rows):
    return json.dumps({"clips": list(rows)})


def test_the_list_path_pages_by_offset():
    assert library_path(40) == (
        f"/clips/auth-user?limit={LIBRARY_PAGE}&offset=40&filter=generations"
        "&include_disliked=false")


def test_a_finished_instrumental_song():
    [song] = library_page(listing(row()))
    assert song == LibrarySong(
        clip_id=A, title="Morning", created="2026-11-07T04:27:58.504133Z",
        seconds=176.9, model=None, status="done", has_lyrics=False,
        prompt="upbeat lofi beat with soft piano", lyrics="[Instrumental]",
        audio_url=f"{BASE}/{A}.m4a", wav_url=f"{BASE}/{A}.wav", session_id=B)


def test_a_song_with_words_has_lyrics():
    [song] = library_page(listing(row(lyrics="[Verse]\nSun on the sill")))
    assert song.has_lyrics and song.lyrics == "[Verse]\nSun on the sill"


def test_a_missing_lyrics_record_means_no_lyrics():
    record = row()
    record["lyrics"] = None
    [song] = library_page(listing(record))
    assert not song.has_lyrics and song.lyrics is None


def test_the_model_is_read_when_the_record_names_it():
    operation = {"sound_prompt": "x", "model": "Lyria 3 Pro"}
    [song] = library_page(listing(row(operation=operation)))
    assert song.model == "Lyria 3 Pro"


@pytest.mark.parametrize("duration, audio, status", [
    ({"status": "completed", "value": "12.5"}, True, "done"),
    ({"status": "pending"}, False, "in_progress"),
    ({"status": "running"}, False, "in_progress"),
    ({"status": "completed", "value": "12.5"}, False, "in_progress"),
    ({"status": "failed"}, False, "failed"),
    ({"status": "error"}, False, "failed"),
])
def test_status(duration, audio, status):
    [song] = library_page(listing(row(duration=duration, audio=audio)))
    assert song.status == status


def test_a_song_still_running_has_no_length():
    [song] = library_page(listing(row(duration={"status": "pending"}, audio=False)))
    assert song.seconds is None


def test_deleted_songs_are_left_out():
    songs = library_page(listing(row(A), row(B, deleted="2026-11-07T05:00:00Z")))
    assert [s.clip_id for s in songs] == [A]


def test_an_empty_page_is_the_end():
    assert library_page('{"clips": []}') == []


@pytest.mark.parametrize("text", ["", "[]", '{"detail": "x"}', '{"clips": {"a": 1}}',
                                  '{"clips": [1]}'])
def test_a_list_reply_of_another_shape_is_schema_drift(text):
    with pytest.raises(SchemaDriftError):
        library_page(text)


def test_a_row_without_an_id_is_schema_drift():
    record = row()
    del record["id"]
    with pytest.raises(SchemaDriftError):
        library_page(listing(record))


def test_a_clip_record_by_id():
    text = json.dumps({"clips": {A: row()}})
    assert clip_record(text, A).clip_id == A


def test_an_unknown_clip_is_none():
    assert clip_record(json.dumps({"clips": {C: None}}), C) is None


def test_a_clip_reply_of_another_shape_is_schema_drift():
    with pytest.raises(SchemaDriftError):
        clip_record('{"clips": []}', A)


def test_search_matches_title_prompt_and_lyrics_in_any_case():
    [song] = library_page(listing(row(lyrics="[Verse]\nSun on the sill")))
    assert song_matches(song, "MORNING")
    assert song_matches(song, "lofi")
    assert song_matches(song, "sun on")
    assert not song_matches(song, "jazz")
    assert song_matches(song, "  ")


def test_a_clip_id_must_be_a_uuid():
    from aistudio._flow.music import check_clip_id

    assert check_clip_id(f" {A.upper()} ") == A
    with pytest.raises(ValidationError, match="not a Flow Music song id"):
        check_clip_id("not-a-uuid")
