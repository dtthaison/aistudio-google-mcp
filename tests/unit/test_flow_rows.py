import json

from aistudio._flow.rows import (
    Plan,
    Quote,
    credits_left,
    media_records,
    parse_permission,
    parse_plan,
)
from aistudio._flow.wire import decode_batch, payloads

MEDIA = "3fc10f7b-49f4-4e72-89f8-620e60a8ae36"
PROJECT = "72df29b1-e119-4741-ad44-ba18bc1ad34a"
VIDEO = f"https://flow-content.google/video/{MEDIA}?Expires=1&KeyName=k&Signature=s"
IMAGE = f"https://flow-content.google/image/{MEDIA}?Expires=1&KeyName=k&Signature=s"


CREATED = 1791366665


def record(status=3, with_links=True, media_id=MEDIA, created=CREATED):
    details = [[created, 0], "an apple", None, None, None, None,
               [None, [["veo_3_1_t2v_lite", 1, None, None, 2, 1]], [["an apple"]], None, 1],
               None, [status], 1, IMAGE if with_links else None, [VIDEO] if with_links else None,
               None, 1285983]
    extra = [[None, 392475, None, None, None, None, None, "<root/>", None, None, None, None,
              "veo_3_1_t2v_lite", "", None, False, 2], [None, None, [8]], [MEDIA]]
    return [media_id, PROJECT, "wf", None, None, details, None, extra]


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
    assert done.created == CREATED


def test_unknown_or_malformed_payloads_give_nothing():
    assert media_records("jwpduf", None) == []
    assert media_records("as29s", ["x"]) == []
    assert media_records("other", record()) == []


def test_credits_left_reads_the_poll_payload():
    assert credits_left([None, 1040, []]) == 1040
    assert credits_left(None) is None


def test_parse_plan_from_the_agent_text():
    text = "I'm going to generate an 8-second video of a red apple using Veo 3.1 - Lite."
    assert parse_plan(text) == Plan(model="Veo 3.1 - Lite", seconds=8)
    assert parse_plan("Here you go.") == Plan(model=None, seconds=None)


def permission(videos=1, images=0, cost=10):
    return [[None, [["c", None, None, ["open_chat_panel", "open_chat_panel", [[["_synthetic",
             [None, None, None, True]]]]]], ["c", None, None, ["ask_for_permission",
             "ask_for_permission", [[["num_videos", [None, videos]], ["num_images", [None, images]],
                                     ["total_cost", [None, cost]], ["num_total",
                                                                    [None, videos + images]]]]]]],
             "c"], True]


def generated_call(placeholder="ph1", model="\U0001f34c Nano Banana 2.1", aspect="16:9"):
    """A free generation's tool call (live shape, plan F findings)."""
    pairs = [["model_usage_key", [None, None, "BELUGA"]], ["aspect_ratio", [None, None, aspect]],
             ["placeholder_frontend_id", [None, None, placeholder]],
             ["prompt", [None, None, "a lemon"]], ["model_display_name", [None, None, model]]]
    return [[[[["text", [None, None, "I'm going to generate"]]]],
             [["e1", None, None, ["generate_image", "generate_image", [pairs], placeholder]]],
             "e1"]]


def generated_result(media_id="g1", placeholder="ph1", status="success"):
    pairs = [["media_id", [None, None, media_id]],
             ["placeholder_frontend_id", [None, None, placeholder]],
             ["display_name", [None, None, "Yellow lemon"]], ["status", [None, None, status]]]
    return [[None, [["e2", None, None, None, ["generate_image", "generate_image", [pairs],
                                               placeholder]]], "e2"]]


def stream(*items):
    lines = [")]}'", ""]
    for item in items:
        line = json.dumps([["wrb.fr", None, json.dumps(item)]])
        lines += [str(len(line)), line]
    return "\n".join(lines + ["26", '[["e",9,null,null,1309]]', ""])


def test_payloads_keeps_every_entry_in_order():
    text = stream([1], [2])
    assert [p for _, p in payloads(text)] == [[1], [2]]


def test_parse_permission_reads_flows_structured_request():
    # Live StreamChat: ask_for_permission carries num_videos, num_images and total_cost only.
    assert parse_permission(permission()) == Quote(count=1, kind="video", credits=10)
    assert parse_permission(permission(videos=0, images=2, cost=4)) == Quote(2, "image", 4)
    assert parse_permission(permission(videos=1, images=1, cost=12)).kind == "mixed"
    assert parse_permission([[None, [["c", None, None, ["open_chat_panel"]]]]]) is None



def test_generated_results_are_read_from_the_stream():
    from aistudio._flow.rows import agent_generated

    got = agent_generated([generated_call(), generated_result(),
                           generated_call("ph2"), generated_result("g2", "ph2", "failed")])
    assert [(g.media_id, g.status, g.kind, g.model, g.aspect) for g in got] == [
        ("g1", "success", "image", "Nano Banana 2.1", "16:9"),
        ("g2", "failed", "image", "Nano Banana 2.1", "16:9")]
    assert agent_generated([permission()]) == []



def test_a_record_names_its_workflow():
    # Live (plan G): the project's workflow list holds only the newest few; each media record
    # carries its own workflow id.
    from aistudio._flow.rows import media_records

    (media,) = media_records("as29s", record())
    assert media.workflow_id == "wf"



def test_a_later_node_does_not_overwrite_the_call():
    # Plan F review M2: a progress node without media_id must not erase the call's model.
    from aistudio._flow.rows import agent_generated

    progress = [[None, [["e3", None, None, ["generate_image", "generate_image",
                                            [[["placeholder_frontend_id", [None, None, "ph1"]],
                                              ["progress", [None, None, "50%"]]]], "ph1"]]],
                 "e3"]]
    result = generated_result()
    pairs = result[0][1][0][4][2][0]
    result[0][1][0][4][2][0] = [p for p in pairs if p[0] != "placeholder_frontend_id"]
    (made,) = agent_generated([generated_call(), progress, result])  # joined by node[3]
    assert (made.model, made.aspect) == ("Nano Banana 2.1", "16:9")
