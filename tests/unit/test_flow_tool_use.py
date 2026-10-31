import pytest

from aistudio._flow.tool_use import (
    Control,
    Step,
    free_path,
    parse_steps,
    ready,
    resolve,
    safe_name,
)
from aistudio.exceptions import ValidationError

CONTROLS = [Control(1, "button", "EN"), Control(2, "button", "Tiếng Việt"),
            Control(3, "button", "PNG"), Control(4, "button", "Markdown (.md)"),
            Control(5, "file", "Upload File"),
            Control(6, "range", "Chart Width (Stitches)", value="60", min="20", max="200"),
            Control(7, "range", "Yarn Colors", value="8", min="2", max="25"),
            Control(8, "button", "Mark row as completed"),
            Control(9, "button", "Mark row as completed")]


def test_steps_are_a_number_or_a_label_with_an_optional_value():
    assert parse_steps(["3", "6=40", "Chart Width=40", "PNG", "Note=a=b"]) == [
        Step(3, None), Step(6, "40"), Step("Chart Width", "40"), Step("PNG", None),
        Step("Note", "a=b")]


def test_an_empty_step_is_refused():
    with pytest.raises(ValidationError, match="empty step"):
        parse_steps(["=4"])


def test_a_number_picks_that_control():
    assert resolve(CONTROLS, 6).label == "Chart Width (Stitches)"


def test_a_label_matches_exactly_before_partly():
    assert resolve(CONTROLS, "png").n == 3
    assert resolve(CONTROLS, "Markdown").n == 4
    assert resolve(CONTROLS, "chart width").n == 6


def test_a_label_on_several_controls_asks_for_a_number():
    with pytest.raises(ValidationError, match="2 controls") as err:
        resolve(CONTROLS, "Mark row")
    assert "8, 9" in err.value.hint


def test_an_unknown_control_is_refused():
    with pytest.raises(ValidationError, match="no control"):
        resolve(CONTROLS, "Download")
    with pytest.raises(ValidationError, match="no control 12"):
        resolve(CONTROLS, 12)


def test_download_names_stay_in_the_folder(tmp_path):
    assert safe_name("../../etc/bear.md") == "bear.md"
    assert safe_name("") == "download"
    assert safe_name("..") == "download"


def test_an_existing_file_is_kept(tmp_path):
    (tmp_path / "bear.md").write_text("mine", encoding="utf-8")
    (tmp_path / "bear-2.md").write_text("mine", encoding="utf-8")
    assert free_path(tmp_path, "bear.md", force=False) == tmp_path / "bear-3.md"
    assert free_path(tmp_path, "bear.md", force=True) == tmp_path / "bear.md"
    assert free_path(tmp_path, "new.png", force=False) == tmp_path / "new.png"


def test_at_waits_for_a_control_and_question_mark_waits_for_text():
    assert parse_steps(["@Make keyframes", "?4 / 4", "@7"]) == [
        Step("Make keyframes", None, wait="control"), Step("4 / 4", None, wait="text"),
        Step(7, None, wait="control")]


def test_a_wait_step_takes_no_value_and_needs_a_target():
    with pytest.raises(ValidationError, match="takes no value"):
        parse_steps(["@Export=1"])
    with pytest.raises(ValidationError, match="empty step"):
        parse_steps(["?"])


def test_a_control_wait_is_ready_once_the_control_is_there_and_enabled():
    step = Step("Make video", None, wait="control")
    assert ready([], "", step) is False
    assert ready([Control(1, "button", "Make video", disabled=True)], "", step) is False
    assert ready([Control(1, "button", "Make video")], "", step) is True


def test_a_control_wait_on_several_controls_asks_for_a_better_label():
    with pytest.raises(ValidationError, match="2 controls"):
        ready(CONTROLS, "", Step("Mark row", None, wait="control"))


def test_a_text_wait_is_ready_once_the_screen_shows_the_text():
    step = Step("4 / 4 ready", None, wait="text")
    assert ready([], "Images: 3 / 4 ready", step) is False
    assert ready([], "Images: 4 / 4 READY", step) is True
