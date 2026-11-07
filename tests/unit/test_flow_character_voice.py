"""Character voices and the body-mode retry (live, 2026-11-02; findings "Flow character voices")."""

import dataclasses
import json
import re

import pytest

from aistudio._app.flow import flow_character_voice, flow_characters, flow_voices
from aistudio._flow.page import (
    SAVE_VOICE,
    SEARCH_ASSETS,
    VOICE_BUTTON,
    VOICE_NAME,
    VOICE_STYLE,
    FlowPage,
)
from aistudio._flow.rows import (
    FlowCharacter,
    FlowVoice,
    character_voice,
    decode_contents,
    saved_voice,
)
from aistudio._flow.voices import PRESET_VOICES, preset_voice
from aistudio.exceptions import BrowserError, NotFoundError, ValidationError
from tests.unit.test_app_flow_d import CHARS, FULL, harness, lists, profile_with_project
from tests.unit.test_flow_page import PID, Clock
from tests.unit.test_flow_page_d import rpc

CID = "a4b260b3-3896-4d9f-841d-60fec8fc2209"
VOICE_ID = "d14e0551-aad9-4ecb-be1a-55bd152422f9"
WORKFLOW = "23602589-a00c-4296-a7ee-e2d10da71b5f"
STYLE = "Upbeat, confident female fitness coach."
FIELD = [["entity_info.character_info.audio_references"]]

# Live rows (trimmed): Mila's record with a custom voice, and that voice's audio media.
CHARACTER_ROW = [PID, CID, None, [1, "Mila Vance", [[["4ac73614"]], [[None, VOICE_ID]], "Upbeat"]],
                 "9a3cbf54", [1376, 768], [1793601289, 14534000], [1793602921, 697558000]]
VOICE_ROW = [VOICE_ID, PID, WORKFLOW, None, None,
             [[1793602860, 24659000], None, None, None, None, None, [None, None, None, None, 1],
              None, None, 1], None, None, None, None,
             [[None, None, None, None, None, None, "Hi there!", STYLE, "### DIRECTOR'S NOTES",
               "models/gemini-v4s-tts-flow", "audio/wav", [["Laomedeia", "Mila voice"]]]]]
VOICE_WORKFLOW = [WORKFLOW, None, None, ["Mila voice", [1793602860, 24659000], None, None,
                                         VOICE_ID, None, [1793602880, 187069000]], PID]


def mila(voice):
    info = [[["4ac73614"]], [[None, voice]] if voice else [], "Upbeat"]
    return [[PID, CID, None, [1, "Mila Vance", info], "9a3cbf54"]]


# --- rows -------------------------------------------------------------------------------------

def test_the_project_listing_carries_voices_and_each_characters_voice():
    contents = decode_contents([None, [VOICE_WORKFLOW], [VOICE_ROW], [], [], [CHARACTER_ROW]])
    assert contents.characters == [FlowCharacter(CID, "Mila Vance", "9a3cbf54", 1793601289,
                                                 VOICE_ID, portrait_workflow="4ac73614")]
    assert contents.voices == [FlowVoice(VOICE_ID, "Mila voice", "Laomedeia", STYLE,
                                         1793602860)]


def test_a_character_without_a_voice_and_media_that_is_no_voice():
    row = [PID, "c2", None, [1, "Boat", [[["r1"]], [], "Calm"]], "i2", None, [5, 0]]
    contents = decode_contents([None, [], [["m1", PID, "w1", None, None, [[1, 0]]]], [], [],
                                [row, [PID, "c3", None, [1, "Old"]]]])
    assert [c.voice for c in contents.characters] == [None, None] and contents.voices == []


def test_a_renamed_voice_shows_its_workflow_title():
    renamed = [WORKFLOW, None, None, ["Coach", [1, 0], None, None, VOICE_ID], PID]
    contents = decode_contents([None, [renamed], [VOICE_ROW], [], [], []])
    assert contents.voices[0].name == "Coach"


def test_a_voice_sample_is_audio_media():
    # Live: "flow media --type image" listed "Mila voice"; its record says audio/wav.
    contents = decode_contents([None, [VOICE_WORKFLOW], [VOICE_ROW], [], [], []])
    assert [m.audio for m in contents.media] == [True]


async def test_voices_are_listed_as_audio_and_never_taken_for_images(logged_in_profile):
    from aistudio._app.flow import flow_media

    profile = profile_with_project(logged_in_profile)
    sample = dataclasses.replace(FULL.media[0], media_id=VOICE_ID, audio=True, model_key=None,
                                 seconds=None)
    kw, _ = voice_harness(dataclasses.replace(WITH_VOICES, media=[*FULL.media, sample]))
    images = await flow_media(profile, kind="image", **kw)
    assert VOICE_ID not in [m["media_id"] for m in images["media"]]
    audio = await flow_media(profile, kind="audio", **kw)
    assert [m["media_id"] for m in audio["media"]] == [VOICE_ID]


async def test_a_voice_is_no_photo_and_no_download(logged_in_profile, tmp_path):
    from aistudio._app.flow import flow_character, flow_download

    profile = profile_with_project(logged_in_profile)
    sample = dataclasses.replace(FULL.media[0], media_id=VOICE_ID, audio=True, model_key=None,
                                 seconds=None)
    contents = dataclasses.replace(WITH_VOICES, media=[*FULL.media, sample])
    kw, _ = voice_harness(contents)
    with pytest.raises(ValidationError) as info:
        await flow_download(profile, VOICE_ID, out=tmp_path / "v.wav", **kw)
    assert "voice sample" in info.value.message
    kw, pages = voice_harness(contents)
    with pytest.raises(ValidationError) as info:
        await flow_character(profile, "Voice face", photo=VOICE_ID, out=tmp_path / "f.png", **kw)
    assert "not an image" in info.value.message


def test_save_and_rpc_replies():
    assert saved_voice([[[VOICE_ID, PID, WORKFLOW, None]], [[WORKFLOW]]]) == VOICE_ID
    assert saved_voice([]) is None
    assert character_voice(mila("laomedeia")) == "laomedeia"
    assert character_voice(mila(None)) is None


def test_presets_are_the_thirty_dialog_voices():
    assert len(PRESET_VOICES) == 30 and len({v.ref for v in PRESET_VOICES}) == 30
    assert preset_voice(" LAOMEDEIA ").ref == "laomedeia"
    assert preset_voice("Mila voice") is None


# --- page -------------------------------------------------------------------------------------

async def test_body_mode_is_clicked_again_until_it_shows(fake_ui):
    # Live: Flow ignores "Create body" until the character has loaded.
    from aistudio._flow.page import BODY_CHIP, CHARACTER_MODEL

    ui = fake_ui(inner={("button", CHARACTER_MODEL): ["Nano Banana 2.1 arrow_drop_down"]},
                 url=f"https://flow.google.com/project/{PID}/character/c1")
    ui.counts = {BODY_CHIP: [0, 0, 0, 1]}  # the body chip turns active after the third click
    assert await FlowPage(ui, now=Clock()).open_body(PID, "c1") == "Nano Banana 2.1"
    assert ui.ops.count(("dispatch", "button", "Create body")) == 3


@pytest.mark.parametrize("ref, info", [("laomedeia", [None, [[None, "laomedeia"]]]),
                                       (VOICE_ID, [None, [[None, VOICE_ID]]]),
                                       (None, [])])
async def test_set_voice_sends_the_field_mask_and_reads_it_back(fake_ui, ref, info):
    ui = fake_ui({"rzMKMb": [rpc("rzMKMb", mila(ref))]})
    await FlowPage(ui).set_voice(PID, CID, ref)
    assert ("batch_rpc", "rzMKMb",
            json.dumps([[PID, CID, None, [1, None, info]], FIELD])) in ui.ops


@pytest.mark.parametrize("reply", [mila("puck"), mila(None), [[PID, "other", None, [1, "X", []]]]])
async def test_a_voice_flow_did_not_store_is_an_error(fake_ui, reply):
    ui = fake_ui({"rzMKMb": [rpc("rzMKMb", reply)]})
    with pytest.raises(BrowserError):
        await FlowPage(ui).set_voice(PID, CID, "laomedeia")


OPTION = "voice_selection\nLaomedeia\nFemale, upbeat, mid-high pitch"  # an option's text (live)


def dialog_ui(fake_ui, responses, first=OPTION):
    return fake_ui(responses, labels={("option", "."): [[first]]},
                   url=f"https://flow.google.com/project/{PID}/character/{CID}")


SAVED = [[[VOICE_ID, PID, WORKFLOW, None]], [[WORKFLOW]]]
DESCRIPTION = "Female, upbeat, mid-high pitch"


async def test_save_voice_drives_the_dialog_like_a_person(fake_ui):
    ui = dialog_ui(fake_ui, {"no0P6": [rpc("no0P6", SAVED)], "mYWVGd": [rpc("mYWVGd", [])]})
    page = FlowPage(ui)
    assert await page.save_voice(PID, CID, "Laomedeia", STYLE, "Mila voice",
                                 description=DESCRIPTION) == (VOICE_ID, WORKFLOW)
    steps = [op for op in ui.ops if op[0] != "wait_for"]
    assert steps == [("goto", f"https://flow.google.com/project/{PID}/character/{CID}"),
                     ("click_selector", VOICE_BUTTON),
                     ("fill", SEARCH_ASSETS, "Laomedeia"),
                     ("fill", "textbox", VOICE_STYLE, STYLE),
                     ("fill", VOICE_NAME, "Mila voice"),
                     ("dispatch", "button", SAVE_VOICE)]
    assert page.started and page.paid_media == [VOICE_ID]


async def test_save_voice_names_the_voice_when_the_page_did_not(fake_ui):
    ui = dialog_ui(fake_ui, {"no0P6": [rpc("no0P6", SAVED)]})
    await FlowPage(ui).save_voice(PID, CID, "Laomedeia", STYLE, "Mila voice",
                                  description=DESCRIPTION)
    sent = {op[1]: json.loads(op[2]) for op in ui.ops if op[0] == "batch_rpc"}
    assert sent["mYWVGd"] == [[WORKFLOW, None, None, ["Mila voice"], PID],
                              [["metadata.display_name"]]]
    assert sent["lt8g5"][0][0] == VOICE_ID and sent["lt8g5"][0][5][9] == 1


async def test_save_voice_refuses_when_the_search_lists_another_voice_first(fake_ui):
    ui = dialog_ui(fake_ui, {}, first="voice_selection settings_2 Laomedeia custom Bright")
    page = FlowPage(ui)
    with pytest.raises(ValidationError) as info:
        await page.save_voice(PID, CID, "Laomedeia", STYLE, "Mila voice",
                              description=DESCRIPTION)
    assert "nothing was saved" in info.value.message and not page.started
    assert not any(op[:2] == ("dispatch", "button") for op in ui.ops)


async def test_a_save_flow_did_not_confirm_is_an_error_after_start(fake_ui):
    ui = dialog_ui(fake_ui, {})
    page = FlowPage(ui)
    with pytest.raises(BrowserError) as info:
        await page.save_voice(PID, CID, "Laomedeia", STYLE, "Mila voice",
                              description=DESCRIPTION)
    assert page.started and "voices" in info.value.hint


# --- app --------------------------------------------------------------------------------------

MILA = FlowCharacter(CID, "Mila Vance", "i9", 120, VOICE_ID)
VOICE = FlowVoice(VOICE_ID, "Mila voice", "Laomedeia", STYLE, 1793602860)
TWIN = FlowVoice("v2", "mila VOICE", "Puck", "Calm", 1)
WITH_VOICES = dataclasses.replace(FULL, characters=[*CHARS, MILA], voices=[VOICE])


def voice_harness(contents=WITH_VOICES, *, save_error=None, set_error=None, credits=(1050,)):
    kw, pages, opened = harness(contents=contents, credits=credits)
    factory = kw["page_factory"]

    def make(ui):
        page = factory(ui)

        async def set_voice(pid, cid, ref):
            page.calls.append(("set_voice", cid, ref))
            if set_error:
                raise set_error

        async def save_voice(pid, cid, base, style, name, *, description):
            page.calls.append(("save_voice", cid, base, style, name, description))
            page.started, page.paid_media = True, ["v9"]
            if save_error:
                raise save_error
            return "v9", "w9"

        page.set_voice, page.save_voice = set_voice, save_voice
        return page

    kw["page_factory"] = make
    return lists(kw), pages


async def test_presets_and_custom_voices_by_name_or_id(logged_in_profile):
    profile = profile_with_project(logged_in_profile)
    kw, pages = voice_harness()
    done = await flow_character_voice(profile, "c1", "puck", **kw)
    assert ("set_voice", "c1", "puck") in pages[-1].calls and done["voice"]["name"] == "Puck"
    for value in ("MILA voice", VOICE_ID):
        done = await flow_character_voice(profile, "c1", value, **kw)
        assert ("set_voice", "c1", VOICE_ID) in pages[-1].calls
        assert done["voice"]["kind"] == "custom" and "created_voice" not in done
    done = await flow_character_voice(profile, "c1", None, clear=True, **kw)
    assert ("set_voice", "c1", None) in pages[-1].calls and done["voice"] is None


@pytest.mark.parametrize("args, error", [
    (("c1", None), ValidationError),  # neither VOICE nor --clear
    (("c1", "puck", {"clear": True}), ValidationError),
    (("c1", "Mila voice", {"style": "calm"}), ValidationError),  # --style needs a preset
    (("c1", "puck", {"voice_name": "X"}), ValidationError),  # --voice-name needs --style
    (("c1", "puck", {"style": "  "}), ValidationError),
    (("c1", "nobody"), NotFoundError),
    (("c9", "puck"), NotFoundError),
])
async def test_bad_voice_requests_change_nothing(logged_in_profile, args, error):
    profile = profile_with_project(logged_in_profile)
    kw, pages = voice_harness()
    options = args[2] if len(args) > 2 else {}
    with pytest.raises(error):
        await flow_character_voice(profile, args[0], args[1], **options, **kw)
    assert not any(c[0] in ("set_voice", "save_voice") for p in pages for c in p.calls)


async def test_two_custom_voices_with_one_name_need_the_media_id(logged_in_profile):
    profile = profile_with_project(logged_in_profile)
    kw, pages = voice_harness(dataclasses.replace(WITH_VOICES, voices=[VOICE, TWIN]))
    with pytest.raises(ValidationError) as info:
        await flow_character_voice(profile, "c1", "mila voice", **kw)
    assert "media id" in info.value.hint
    await flow_character_voice(profile, "c1", "v2", **kw)
    assert ("set_voice", "c1", "v2") in pages[-1].calls


async def test_a_style_saves_a_new_voice_then_sets_it(logged_in_profile):
    profile = profile_with_project(logged_in_profile)
    kw, pages = voice_harness()
    done = await flow_character_voice(profile, "c1", "Puck", style=" Calm,\n slow ", **kw)
    calls = pages[-1].calls
    assert ("save_voice", "c1", "Puck", "Calm, slow", "Paper boat voice",
            "Male, upbeat, mid pitch") in calls
    assert calls.index(("set_voice", "c1", "v9")) > calls.index(next(
        c for c in calls if c[0] == "save_voice"))
    assert done["created_voice"] == {"media_id": "v9", "name": "Paper boat voice",
                                     "credits_spent": 0}
    assert done["voice"]["name"] == "Paper boat voice" and "warnings" not in done


async def test_a_style_refuses_a_voice_name_already_taken(logged_in_profile):
    profile = profile_with_project(logged_in_profile)
    kw, pages = voice_harness()
    with pytest.raises(ValidationError) as info:
        await flow_character_voice(profile, CID, "Laomedeia", style="calm",
                                   voice_name=" mila  VOICE", **kw)
    assert VOICE_ID in info.value.hint
    assert not any(c[0] == "save_voice" for c in pages[-1].calls)


async def test_a_charged_save_is_reported(logged_in_profile):
    profile = profile_with_project(logged_in_profile)
    kw, _ = voice_harness(credits=(1050, 1040))
    done = await flow_character_voice(profile, "c1", "Puck", style="calm", voice_name="V", **kw)
    assert done["created_voice"]["credits_spent"] == 10
    assert any("fell by 10" in w for w in done["warnings"])


async def test_failures_after_the_save_forbid_a_second_save(logged_in_profile):
    profile = profile_with_project(logged_in_profile)
    kw, _ = voice_harness(save_error=BrowserError("lost", hint="check Flow"))
    with pytest.raises(BrowserError) as info:
        await flow_character_voice(profile, "c1", "Puck", style="calm", voice_name="V", **kw)
    assert "do not re-run with --style" in info.value.hint and "v9" in info.value.hint
    kw, _ = voice_harness(set_error=BrowserError("not stored"))
    with pytest.raises(BrowserError) as info:
        await flow_character_voice(profile, "c1", "Puck", style="calm", voice_name="V", **kw)
    assert "aistudio flow character voice c1 v9" in info.value.hint
    kw, _ = voice_harness(set_error=BrowserError("not stored", hint="retry"))
    with pytest.raises(BrowserError) as info:  # no new voice: the page's own hint stands
        await flow_character_voice(profile, "c1", "puck", **kw)
    assert info.value.hint == "retry"


async def test_voice_listing_and_search(logged_in_profile):
    profile = profile_with_project(logged_in_profile)
    kw, _ = voice_harness()
    every = await flow_voices(profile, **kw)
    assert every["count"] == 31 and every["voices"][0]["kind"] == "custom"
    found = await flow_voices(profile, search="UPBEAT", **kw)
    assert [v["name"] for v in found["voices"]] == ["Mila voice", "Laomedeia", "Puck"]
    assert [v["name"] for v in (await flow_voices(profile, search="laomedeia", **kw))["voices"]] \
        == ["Mila voice", "Laomedeia"]  # a custom voice is found by its base too


async def test_the_character_list_names_each_voice(logged_in_profile):
    profile = profile_with_project(logged_in_profile)
    odd = FlowCharacter("c5", "Odd", "i5", 1, "gone-id")
    kw, _ = voice_harness(dataclasses.replace(
        WITH_VOICES, characters=[MILA, dataclasses.replace(CHARS[0], voice="kore"), odd,
                                 CHARS[3]]))
    listed = {c["id"]: c["voice"] for c in (await flow_characters(profile, **kw))["characters"]}
    assert listed[CID]["name"] == "Mila voice" and listed["c1"]["name"] == "Kore"
    assert listed["c5"] == {"name": None, "kind": "unknown", "ref": "gone-id"}
    assert listed["c4"] is None


def test_option_labels_are_compared_without_icon_words():
    label = "voice_selection\nLaomedeia\nFemale, upbeat, mid-high pitch"
    assert " ".join(label.replace("voice_selection", " ").split()) == f"Laomedeia {DESCRIPTION}"
    assert re.compile(".").search(label)
