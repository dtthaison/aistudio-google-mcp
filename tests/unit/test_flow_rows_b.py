from aistudio._flow.rows import (
    FlowProject,
    character_id,
    decode_contents,
    decode_projects,
    extend_started,
    media_records,
)
from tests.unit.test_flow_rows import CREATED, MEDIA, PROJECT, record

IMAGE = "https://flow-content.google/image/i1?Signature=s"


def test_projects_with_titles():
    payload = [[["p1", ["aistudio", None, [1791371089, 0], "https://x/y", "m1"]],
                ["p2", ["Oct 07 - 13:57", None, [1791356229, 0]]], ["bad"]]]
    assert decode_projects(payload) == [FlowProject("p1", "aistudio", 1791371089),
                                        FlowProject("p2", "Oct 07 - 13:57", 1791356229)]
    assert decode_projects(None) == []


def test_contents_map_media_to_titles():
    workflow = ["w1", None, None, ["Red apple rolling on table", [CREATED, 0], None, None, MEDIA],
                PROJECT]
    contents = decode_contents([None, [workflow], [record()], []])
    assert contents.titles == {MEDIA: "Red apple rolling on table"}
    assert [m.media_id for m in contents.media] == [MEDIA]
    assert decode_contents(None).media == []


def test_character_id_and_image():
    assert character_id([[PROJECT, "c1", None, [1, "Untitled character", []]]]) == "c1"
    assert character_id(None) is None
    image = ["i1", None, "w1", None, None, [[CREATED, 0], "a robot", None, None, None, None,
                                            None, None, [3], 1, IMAGE]]
    (media,) = media_records("ogiZ0b", [[image]])
    assert media.image_url == IMAGE and media.status == 3


def test_extend_started_reads_credits_and_new_media():
    credits, media = extend_started([None, 1020, [["w2"]], [record(media_id="m2")]])
    assert credits == 1020 and [m.media_id for m in media] == ["m2"]
    assert extend_started(None) == (None, [])


def test_a_character_image_in_its_recorded_shape():
    # Live ogiZ0b (2026-10-08): the link sits in row[6], not in a details list at row[5].
    row = ["5663ce6f-0720-4aff-988e-b7d07351008a", None, "9472694e-e599-455f-b7fb-01f122d155b1",
           None, None, None,
           [[None, 416771161, None, None, None, None, 1, "a robot", 36, None, None,
             "9472694e-e599-455f-b7fb-01f122d155b1", None, IMAGE, 3,
             [None, None, [["a robot", None, [[["a robot"]]]]], []], None,
             "5663ce6f-0720-4aff-988e-b7d07351008a"], None, [1376, 768]]]
    (media,) = media_records("ogiZ0b", [[row], [["9472694e-e599-455f-b7fb-01f122d155b1"]]])
    assert (media.media_id, media.image_url, media.status) == (row[0], IMAGE, 3)
