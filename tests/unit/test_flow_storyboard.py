import copy

import pytest

from aistudio._flow.storyboard import (
    DEFAULT_EXTEND_DURATION,
    EXTEND_MODEL,
    compose_clip_prompt,
    fingerprint,
    parse_storyboard,
    quote_groups,
)
from aistudio.exceptions import ValidationError

PLATE = "11111111-2222-4333-8444-555555555555"
PRODUCT = "66666666-7777-4888-8999-000000000000"

BASE = {
    "style": "Soft morning light, 35mm lens.",
    "wardrobe": "She wears a red hoodie.",
    "characters": [{"name": "Mai"},
                   {"name": "Robo", "create": {"description": "a small friendly robot"}}],
    "locations": [{"name": "gym", "prompt": "An empty gym at dawn"},
                  {"name": "street", "media": PLATE}],
    "shots": [
        {"id": "s1", "prompt": "Mai walks into the gym", "say": "Let's get started",
         "characters": ["Mai"], "location": "gym"},
        {"id": "s2", "join": "extend", "prompt": "She picks up a weight"},
        {"id": "s3", "join": "frame", "prompt": "Close on her hands", "mode": "start_frame"},
        {"id": "s4", "join": "cut", "prompt": "Outside", "location": "street",
         "mode": "start_frame", "keyframe": {"characters": ["Mai"]}},
    ],
}


def board(**changes):
    data = copy.deepcopy(BASE)
    data.update(changes)
    return data


def problems(data) -> list[str]:
    with pytest.raises(ValidationError) as caught:
        parse_storyboard(data)
    return caught.value.message.splitlines()


def test_a_full_storyboard_parses_with_defaults_filled_in():
    sb = parse_storyboard(BASE)
    assert [s.id for s in sb.shots] == ["s1", "s2", "s3", "s4"]
    s1, s2, s3, s4 = sb.shots
    assert (s1.model, s1.duration, s1.aspect) == ("veo-3.1-lite", 8, "16:9")
    assert (s1.mode, s1.join) == ("ingredient", "cut")  # a character picks the mode
    assert s1.keyframe is not None and s1.keyframe.characters == ("Mai",)
    assert s1.keyframe.location == "gym"
    assert s2.join == "extend" and s2.keyframe is None and s2.model == EXTEND_MODEL
    assert s2.duration == DEFAULT_EXTEND_DURATION
    assert s3.join == "frame" and s3.keyframe is None and s3.frame_at is None
    assert s4.mode == "start_frame" and s4.characters == ()
    assert s4.keyframe is not None and s4.keyframe.characters == ("Mai",)


def test_fixed_sentences_are_appended_verbatim_to_every_prompt():
    sb = parse_storyboard(BASE)
    s1 = sb.shots[0]
    assert s1.prompt == ('Mai walks into the gym. Mai says: "Let\'s get started". '
                         'Soft morning light, 35mm lens. She wears a red hoodie.')
    # the keyframe has no speech
    assert s1.keyframe is not None
    assert s1.keyframe.prompt == ("Mai walks into the gym. Soft morning light, 35mm lens. "
                                  "She wears a red hoodie.")
    plate = sb.locations[0]
    assert plate.prompt == "An empty gym at dawn. Soft morning light, 35mm lens."  # style only
    assert sb.locations[1].prompt is None and sb.locations[1].media == PLATE


def test_compose_adds_a_full_stop_only_when_the_prompt_has_no_ending():
    assert compose_clip_prompt("Go!", None, None, ["Style."]) == "Go! Style."
    assert compose_clip_prompt("Go", None, None, ["Style."]) == "Go. Style."
    assert compose_clip_prompt("Go", "Hi", "Mai", []) == 'Go. Mai says: "Hi".'
    assert compose_clip_prompt("Go", "Hi", None, []) == 'Go. The speaker says: "Hi".'


def test_defaults_can_be_set_at_the_top_and_per_shot():
    data = board(model="veo-3.1-fast", duration=6, aspect="9:16")
    data["shots"][0].update(model="veo-3.1-quality", duration=8)
    sb = parse_storyboard(data)
    assert (sb.shots[0].model, sb.shots[0].duration, sb.shots[0].aspect) == (
        "veo-3.1-quality", 8, "9:16")
    assert sb.shots[1].model == EXTEND_MODEL and sb.shots[1].duration == DEFAULT_EXTEND_DURATION
    assert sb.shots[3].duration == 6 and sb.shots[3].model == "veo-3.1-fast"
    # the saved frame and the plate follow the video's aspect
    assert sb.locations[0].aspect == "9:16"


def test_every_problem_is_reported_at_once():
    data = board(shots=[{"prompt": ""}, {"id": "a", "prompt": "x", "model": "nope"}])
    found = problems(data)
    assert len(found) >= 3
    assert any("shot-1" in line and "prompt" in line for line in found)
    assert any("nope" in line for line in found)


def test_unknown_keys_are_refused_so_typos_do_not_pass_silently():
    assert any("charcters" in p for p in problems(board(charcters=[])))
    data = board()
    data["shots"][0]["charecters"] = ["Mai"]
    assert any("charecters" in p for p in problems(data))


def test_a_storyboard_needs_shots():
    assert any("shots" in p for p in problems(board(shots=[])))
    assert any("mapping" in p for p in problems([1, 2]))


def test_character_names_are_unique_ignoring_case_and_spacing():
    data = board(characters=[{"name": "Mai"}, {"name": "  mai "}])
    assert any("unique" in p and "mai" in p.lower() for p in problems(data))


def test_a_character_is_existing_or_created_not_both():
    data = board(characters=[{"name": "Mai", "id": "c1", "create": {"description": "x"}}])
    assert any("Mai" in p and "create" in p for p in problems(data))
    data = board(characters=[{"name": "Mai", "create": {"description": "x", "photo": PLATE}}])
    assert any("exactly one" in p for p in problems(data))
    data = board(characters=[{"name": "Mai", "create": {}}])
    assert any("exactly one" in p for p in problems(data))


def test_a_character_photo_must_be_a_media_id_never_a_local_file():
    data = board(characters=[{"name": "Mai", "create": {"photo": "me.jpg"}}])
    found = problems(data)
    assert any("media id" in p and "rights" in p for p in found)


def test_unknown_character_model_is_refused():
    data = board(characters=[{"name": "Mai", "create": {"description": "x", "model": "dalle"}}])
    assert any("dalle" in p for p in problems(data))


def test_locations_need_exactly_one_of_prompt_or_media_and_unique_names():
    data = board(locations=[{"name": "gym"}, {"name": "Gym", "prompt": "p", "media": PLATE}])
    found = problems(data)
    assert any("gym" in p and "exactly one" in p for p in found)
    assert any("unique" in p for p in found)
    assert any("not a Flow media id" in p for p in problems(
        board(locations=[{"name": "gym", "media": "plate.jpg"}])))


def test_a_shot_must_name_declared_characters_and_locations():
    data = board()
    data["shots"][0]["characters"] = ["Ghost"]
    data["shots"][0]["location"] = "moon"
    found = problems(data)
    assert any("Ghost" in p for p in found) and any("moon" in p for p in found)


def test_shot_ids_are_unique_and_safe_for_file_names():
    data = board()
    data["shots"][1]["id"] = "s1"
    assert any("unique" in p for p in problems(data))
    data = board()
    data["shots"][0]["id"] = "../x"
    assert any("../x" in p for p in problems(data))
    data = board()
    del data["shots"][0]["id"]
    assert parse_storyboard(data).shots[0].id == "shot-1"


def test_the_first_shot_cannot_join_a_previous_one():
    data = board()
    data["shots"][0]["join"] = "extend"
    assert any("first shot" in p for p in problems(data))
    data = board()
    data["shots"][0]["join"] = "frame"
    assert any("first shot" in p for p in problems(data))


def test_a_start_frame_cannot_be_combined_with_a_character():
    data = board()
    data["shots"][0].update(mode="start_frame")  # s1 has characters: ["Mai"]
    found = problems(data)
    assert any("start frame" in p and "character" in p and "s1" in p for p in found)


def test_a_character_clip_takes_at_most_one_ingredient():
    data = board()
    data["shots"][0]["ingredients"] = [PRODUCT]  # the keyframe is already one ingredient
    assert any("at most one ingredient" in p and "s1" in p for p in problems(data))


def test_a_keyframe_with_a_character_takes_at_most_one_ingredient():
    data = board()
    data["shots"][0]["keyframe"] = {"ingredients": [PRODUCT]}  # plate + product, with Mai
    assert any("keyframe" in p and "at most one ingredient" in p for p in problems(data))
    data["shots"][0].pop("location")
    assert parse_storyboard(data).shots[0].keyframe.ingredients == (PRODUCT,)


def test_ingredients_must_be_media_ids():
    data = board()
    data["shots"][3]["ingredients"] = ["logo.png"]
    assert any("logo.png" in p for p in problems(data))


def test_start_frame_with_ingredients_is_not_measured_and_refused():
    data = board()
    data["shots"][3]["ingredients"] = [PRODUCT]
    assert any("start frame" in p and "ingredient" in p and "not measured" in p
               for p in problems(data))


def test_an_extension_carries_only_a_prompt_a_line_and_an_optional_length():
    for key, value in [("mode", "start_frame"), ("characters", ["Mai"]), ("location", "gym"),
                       ("aspect", "9:16"), ("model", "veo-3.1-fast"),
                       ("keyframe", "x"), ("ingredients", [PRODUCT]), ("frame_at", 1),
                       ("duration", 2), ("duration", 11)]:
        data = board()
        data["shots"][1][key] = value
        assert any("s2" in p and key in p and "extend" in p for p in problems(data)), key
    data = board()
    data["shots"][1].update(say="More", duration=6, model=EXTEND_MODEL)
    sb = parse_storyboard(data)
    assert sb.shots[1].duration == 6 and sb.shots[1].model == EXTEND_MODEL


def test_extending_works_after_veo_and_omni_clips():
    for model in ("veo-3.1-lite", "omni-1.1-flash", "omni-1.2-flash"):
        data = board()
        data["shots"][0].update(model=model, duration=8)
        sb = parse_storyboard(data)
        assert sb.shots[1].join == "extend" and sb.shots[1].model == EXTEND_MODEL


def test_a_frame_join_keeps_the_aspect_of_the_clip_before():
    data = board()
    data["shots"][2]["aspect"] = "9:16"
    assert any("s3" in p and "aspect" in p for p in problems(data))


def test_frame_at_only_goes_with_a_frame_join_and_is_not_negative():
    data = board()
    data["shots"][2]["frame_at"] = 2.5
    assert parse_storyboard(data).shots[2].frame_at == 2.5
    data["shots"][2]["frame_at"] = -1
    assert any("frame_at" in p for p in problems(data))
    data = board()
    data["shots"][3]["frame_at"] = 1
    assert any("frame_at" in p and "frame" in p for p in problems(data))


def test_a_frame_join_has_no_keyframe_and_a_cut_may_use_an_existing_media_id():
    data = board()
    data["shots"][2]["keyframe"] = "x"
    assert any("s3" in p and "keyframe" in p for p in problems(data))
    data = board()
    data["shots"][3]["keyframe"] = {"media": PLATE}
    sb = parse_storyboard(data)
    assert sb.shots[3].keyframe is not None and sb.shots[3].keyframe.media == PLATE
    data["shots"][3]["keyframe"] = {"media": PLATE, "prompt": "x"}
    assert any("keyframe" in p and "media" in p for p in problems(data))


def test_stitch_trims_are_whole_seconds_within_the_clip():
    data = board()
    data["shots"][0]["trim"] = [1, 5]
    assert parse_storyboard(data).shots[0].trim == (1, 5)
    for bad in ([1.5, 5], [0, 9], [5, 5], [3, 1], [1], "2-5", [True, 4]):
        data["shots"][0]["trim"] = bad
        assert any("s1" in p and "trim" in p for p in problems(data)), bad
    data["shots"][0]["trim"] = [0, DEFAULT_EXTEND_DURATION]
    data["shots"][1]["trim"] = [0, DEFAULT_EXTEND_DURATION + 1]  # default extend is 4 s
    assert any("s2" in p and "trim" in p for p in problems(data))
    data["shots"][1].update(duration=7, trim=[0, 7])
    assert parse_storyboard(data).shots[1].trim == (0, 7)


def test_model_duration_and_aspect_are_checked_like_flow_video():
    assert any("veo-3.1-quality" in p and "8" in p for p in problems(
        board(model="veo-3.1-quality", duration=4)))
    assert any("5:4" in p for p in problems(board(aspect="5:4")))
    assert any("nano-banana" in p for p in problems(board(model="nano-banana-2.1")))
    assert any("duration" in p for p in problems(board(duration="8")))


def test_a_long_spoken_line_is_a_warning_not_an_error():
    data = board()
    data["shots"][0]["say"] = " ".join(["word"] * 25)
    sb = parse_storyboard(data)
    assert any("s1" in w and "25 words" in w for w in sb.warnings)


def test_a_character_kept_out_of_the_clip_is_noted_as_voiceless():
    sb = parse_storyboard(BASE)
    assert any("s4" in w and "voice" in w for w in sb.warnings)


def test_project_must_be_a_flow_id_or_link():
    pid = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    assert parse_storyboard(board(project=pid)).project == pid
    assert parse_storyboard(board(project=f"https://flow.google.com/project/{pid}")
                            ).project == pid
    assert any("project" in p for p in problems(board(project="my project")))


def test_quote_groups_share_one_quote_per_model_duration_and_aspect():
    data = board()
    data["shots"][3].update(duration=4)
    sb = parse_storyboard(data)
    groups = quote_groups(sb)
    assert groups == {("veo-3.1-lite", 8, "16:9"): ["s1", "s3"],
                      ("veo-3.1-lite", 4, "16:9"): ["s4"]}  # s2 extends: priced by the table


def test_fingerprints_change_with_content_only():
    sb = parse_storyboard(BASE)
    again = parse_storyboard(copy.deepcopy(BASE))
    assert fingerprint(sb.shots[0]) == fingerprint(again.shots[0])
    other = board()
    other["shots"][0]["prompt"] = "Mai walks out of the gym"
    assert fingerprint(parse_storyboard(other).shots[0]) != fingerprint(sb.shots[0])
    assert fingerprint(parse_storyboard(other).shots[1]) == fingerprint(sb.shots[1])


def test_a_single_shot_cannot_be_trimmed_because_nothing_is_stitched():
    data = board(shots=[{"id": "only", "prompt": "x", "trim": [0, 4]}])
    assert any("only" in p and "trim" in p and "two shots" in p for p in problems(data))
    del data["shots"][0]["trim"]
    assert len(parse_storyboard(data).shots) == 1


def test_the_documented_example_is_a_valid_storyboard():
    from pathlib import Path

    from aistudio._flow.storyboard import load_storyboard

    root = Path(__file__).resolve().parents[2]
    sb = load_storyboard(root / "docs" / "examples" / "flow-storyboard.yaml")
    assert [s.join for s in sb.shots] == ["cut", "extend", "frame", "cut"]
    assert sb.shots[2].trim == (1, 6) and sb.shots[2].frame_at == 3.0


def test_load_storyboard_reports_unreadable_and_non_yaml_files(tmp_path):
    from aistudio._flow.storyboard import load_storyboard

    with pytest.raises(ValidationError):
        load_storyboard(tmp_path / "missing.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("shots: [unclosed")
    with pytest.raises(ValidationError) as caught:
        load_storyboard(bad)
    assert "YAML" in caught.value.message
