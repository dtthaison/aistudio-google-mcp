import json

from aistudio._flow.rows import (
    Plan,
    Quote,
    credits_left,
    media_records,
    parse_plan,
    parse_quote,
)
from aistudio._flow.wire import decode_batch

MEDIA = "3fc10f7b-49f4-4e72-89f8-620e60a8ae36"
PROJECT = "72df29b1-e119-4741-ad44-ba18bc1ad34a"
VIDEO = f"https://flow-content.google/video/{MEDIA}?Expires=1&KeyName=k&Signature=s"
IMAGE = f"https://flow-content.google/image/{MEDIA}?Expires=1&KeyName=k&Signature=s"


def record(status=3, with_links=True):
    details = [[[1791366665, 0]], "an apple", None, None, None, None,
               [None, [["veo_3_1_t2v_lite", 1, None, None, 2, 1]], [["an apple"]], None, 1],
               None, [status], 1, IMAGE if with_links else None, [VIDEO] if with_links else None,
               None, 1285983]
    extra = [[None, 392475, None, None, None, None, None, "<root/>", None, None, None, None,
              "veo_3_1_t2v_lite", "", None, False, 2], [None, None, [8]], [MEDIA]]
    return [MEDIA, PROJECT, "wf", None, None, details, None, extra]


def batch(rpcid, payload):
    inner = json.dumps(payload)
    line = json.dumps([["wrb.fr", rpcid, inner, None, None, None, "generic"], ["di", 5]])
    return f")]}}'\n\n{len(line)}\n{line}\n25\n[[\"e\",4,null,null,315]]\n"


def test_decode_batch_unwraps_each_rpc():
    assert decode_batch(batch("jwpduf", [None, 1040, []])) == {"jwpduf": [None, 1040, []]}
    assert decode_batch("not a batch") == {}


def test_media_records_from_poll_and_detail_calls():
    (polled,) = media_records("jwpduf", [None, 1040, [record(status=2, with_links=False)]])
    (done,) = media_records("as29s", record())
    assert (polled.media_id, polled.status, polled.video_url) == (MEDIA, 2, None)
    assert (done.model_key, done.status, done.seconds, done.size) == ("veo_3_1_t2v_lite", 3, 8,
                                                                     1285983)
    assert (done.video_url, done.image_url, done.project_id) == (VIDEO, IMAGE, PROJECT)


def test_unknown_or_malformed_payloads_give_nothing():
    assert media_records("jwpduf", None) == []
    assert media_records("as29s", ["x"]) == []
    assert media_records("other", record()) == []


def test_credits_left_reads_the_poll_payload():
    assert credits_left([None, 1040, []]) == 1040
    assert credits_left(None) is None


def test_parse_quote_and_plan_from_the_agent_text():
    text = ("I'm going to generate an 8-second video of a red apple using Veo 3.1 - Lite. "
            "Would you like me to kick off this 1 video generation, costing 10 credits?")
    assert parse_quote(text) == Quote(count=1, kind="video", credits=10)
    assert parse_plan(text) == Plan(model="Veo 3.1 - Lite", seconds=8)
    assert parse_quote("kick off these 2 image generations, costing 1,200 credits?") == Quote(
        count=2, kind="image", credits=1200)
    assert parse_quote("Sure! What style would you like?") is None
    assert parse_plan("Here you go.") == Plan(model=None, seconds=None)
