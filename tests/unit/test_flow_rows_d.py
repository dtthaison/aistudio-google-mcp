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



def test_unnamed_characters_are_listed():
    # Plan D review M10: a character Flow lists without a name can still be listed and renamed.
    (character,) = contents(s5=[["p1", "c9", None, [1, "", []], None, None, [1, 0], [1, 0]]]
                            ).characters
    assert (character.id, character.name) == ("c9", "")


def test_a_character_lists_its_portrait_and_body_workflows():
    # Live 2026-11-07: [3][2][0] holds one [workflow] per slot (portrait, then body).
    luna = [PID, "c5", None, [1, "Luna", [[["wp"], ["wb"]], None, "notes"]], "i5", [1, 1],
            [300, 0], [301, 0]]
    chars = {c.id: c for c in contents(s5=[*CHARS, luna]).characters}
    assert (chars["c5"].portrait_workflow, chars["c5"].body_workflow) == ("wp", "wb")
    assert (chars["c1"].portrait_workflow, chars["c1"].body_workflow) == ("w9", None)
    assert (chars["c2"].portrait_workflow, chars["c2"].body_workflow) == (None, None)


def _req(method, args):
    return urlencode({"f.req": json.dumps([[[method, json.dumps(args), None, "generic"]]])})


def test_a_body_request_must_write_the_body_slot_and_spare_the_portrait():
    from aistudio._flow.rows import body_request_target, is_image_request

    to_slot = _req("ogiZ0b", [None, [[None, [None, 22, None, None, None, PID]]], 1,
                              ["u1", None, ["c5", [1]]]])
    to_portrait = _req("ogiZ0b", [None, [[None, [None, 22, None, None, "wp", PID]]], 1, ["u1"]])
    to_body_wf = _req("ogiZ0b", [None, [[None, [None, 22, None, None, "wb", PID]]], 1, ["u1"]])
    to_portrait_slot = _req("ogiZ0b", [None, [[None]], 1, ["u1", None, ["c5", [0]]]])
    assert body_request_target(to_slot, "c5", None, "wp")
    assert not body_request_target(to_portrait, "c5", None, "wp")
    assert not body_request_target(to_portrait_slot, "c5", None, "wp")
    assert not body_request_target(to_slot, "c9", None, "wp")  # another character
    assert body_request_target(to_body_wf, "c5", "wb", "wp")  # a body that has a workflow
    assert not body_request_target(to_slot, "c5", "wb", "wp")
    rename = _req("mYWVGd", [["w-new", None, None, ["Luna body"], PID],
                             [["metadata.display_name"]]])
    primary = _req("mYWVGd", [["wp", None, None, [None, None, None, None, "b1"], PID],
                              [["metadata.primary_media_id"]]])
    assert body_request_target(rename, "c5", None, "wp")  # the page naming the new image
    assert not body_request_target(primary, "c5", None, "wp")  # the portrait's version moving
    assert not body_request_target("f.req=not json", "c5", None, "wp")
    assert is_image_request(to_slot) and not is_image_request(rename)
