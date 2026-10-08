import json
from urllib.parse import urlencode

from aistudio._flow.rows import (
    character_name,
    decode_contents,
    linked_image,
    sent_characters,
    sent_media,
    stitch_job,
    stitch_state,
)

PID = "p1"
CHARS = [[PID, "c1", None, [1, "Paper boat", [[["w9"]]]], "i1", [1376, 768], [100, 0], [101, 0]],
         [PID, "c2", None, [1, "Untitled character", []], None, None, [90, 0], [90, 0]]]
SCENES = [["s1", "Untitled Scene 10-08", None, [200, 0], [201, 0], 2, None, []]]
CLIPS = [[["w2", None, None, ["the camera pulls back", [210, 0], None, None, "m2"], PID], "s1",
          [1, [8], [], [8], [1]]],
         [["w1", None, None, ["Red apple", [205, 0], None, None, "m1"], PID], "s1",
          [None, [8], [2], [6], [1]]]]


def contents(**slots):
    payload = [None, [], [], [], [], [], [], None]
    for index, value in slots.items():
        payload[int(index[1:])] = value
    return decode_contents(payload)


def test_characters_come_from_slot_5():
    got = contents(s5=CHARS).characters
    assert [(c.id, c.name, c.media_id, c.created) for c in got] == [
        ("c1", "Paper boat", "i1", 100), ("c2", "Untitled character", None, 90)]


def test_scenes_list_their_clips_in_position_order():
    (scene,) = contents(s4=SCENES, s6=CLIPS).scenes
    assert (scene.id, scene.title, scene.created) == ("s1", "Untitled Scene 10-08", 200)
    assert [(c.media_id, c.title, c.start, c.end) for c in scene.clips] == [
        ("m1", "Red apple", 2, 6), ("m2", "the camera pulls back", 0, 8)]


def test_a_contents_payload_without_new_slots_still_decodes():
    decoded = decode_contents([None, [], []])
    assert decoded.characters == [] and decoded.scenes == []


def test_stitch_job_and_state():
    job = "projects/1/locations/us/jobs/u1"
    assert stitch_job([None, [[job]]]) == job
    assert stitch_job([None, [["not-a-job"]]]) is None
    assert stitch_state([2, "", "", 0]) == (2, None)
    assert stitch_state([3, "", "", 3, "AAAA"]) == (3, "AAAA")
    assert stitch_state(None) == (None, None)


def test_sent_characters_reads_the_character_slot():
    message = ["sess", [[[["a prompt"]]], None, None, [["c1"], ["c2"]]],
               ["projects/p1", None, ["tok", 1], None, None, 3]]
    body = urlencode({"f.req": json.dumps([None, json.dumps(message)]), "at": "x"})
    assert sent_characters(body) == ["c1", "c2"]
    no_chars = ["sess", [[[["a prompt"]]]], ["projects/p1"]]
    assert sent_characters(urlencode({"f.req": json.dumps([None, json.dumps(no_chars)])})) == []
    assert sent_characters("garbage") == []
    assert sent_characters("") == []


def test_linked_image_and_character_name():
    assert linked_image([["i9", PID, "w9"], ["w9"]]) == "i9"
    assert linked_image([]) is None
    assert character_name([[PID, "c1", None, [1, "Boat", []]]]) == "Boat"
    assert character_name(None) is None


def test_sent_media_reads_slot_1():
    message = ["sess", [[[["a prompt"]]], [["m1"], ["m2", None]]], ["projects/p1"]]
    body = urlencode({"f.req": json.dumps([None, json.dumps(message)])})
    assert sent_media(body) == ["m1", "m2"]
    bare = json.dumps([None, json.dumps(["s", [[[["x"]]]]])])
    assert sent_media(urlencode({"f.req": bare})) == []
