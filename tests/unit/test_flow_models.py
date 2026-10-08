import pytest

from aistudio._flow.models import (
    Decision,
    Expect,
    check_options,
    decide,
    find_model,
    instruction,
)
from aistudio._flow.rows import Plan, Quote
from aistudio.exceptions import ValidationError

LITE = find_model("veo-3.1-lite", "video")
QUOTE = Quote(count=1, kind="video", credits=10)
PLAN = Plan(model="Veo 3.1 - Lite", seconds=8)
EXPECT = Expect(kind="video", count=1, model="Veo 3.1 - Lite", seconds=8)


def test_find_model_by_cli_id_and_kind():
    assert (LITE.label, LITE.durations) == ("Veo 3.1 - Lite", (4, 6, 8))
    assert find_model("NANO-BANANA-PRO", "image").label == "Nano Banana Pro"
    with pytest.raises(ValidationError) as info:
        find_model("veo-3.1-lite", "image")
    assert "flow video" in info.value.hint


@pytest.mark.parametrize("kind,model,aspect,count,duration", [
    ("video", "veo-3.1-quality", None, None, 4),   # Quality is 8 s only
    ("video", None, "4:3", None, None),            # video is 16:9 or 9:16
    ("image", None, None, 5, None),                # 1-4 outputs
    ("image", None, None, None, 8),                # images have no length
])
def test_check_options_rejects_what_flow_does_not_offer(kind, model, aspect, count, duration):
    with pytest.raises(ValidationError):
        check_options(kind, find_model(model, kind) if model else None, aspect, count, duration)


def test_check_options_accepts_valid_choices():
    check_options("video", LITE, "9:16", 4, 6)
    check_options("image", None, "3:4", 2, None)


def test_instruction_is_explicit():
    text = instruction("video", "a red apple", count=2, model_label="Veo 3.1 - Lite", seconds=8,
                       aspect="9:16")
    assert text == ("Generate exactly 2 8-second videos with Veo 3.1 - Lite in 9:16. Do not make "
                    "a storyboard or any other videos. Prompt: a red apple")


@pytest.mark.parametrize("quote,plan,budget,decision", [
    (QUOTE, PLAN, None, Decision.QUOTE_ONLY),
    (QUOTE, PLAN, 10, Decision.APPROVE),
    (QUOTE, PLAN, 9, Decision.REJECT_OVER_BUDGET),
    (Quote(2, "video", 20), PLAN, 50, Decision.REJECT_MISMATCH),        # count
    (Quote(1, "image", 2), PLAN, 50, Decision.REJECT_MISMATCH),         # kind
    (QUOTE, Plan("Veo 3.1 - Fast", 8), 50, Decision.REJECT_MISMATCH),  # model
    (QUOTE, Plan("Veo 3.1 - Lite", 6), 50, Decision.REJECT_MISMATCH),  # length
    (QUOTE, Plan(None, None), 50, Decision.APPROVE),  # unstated: settings were saved and re-read
])
def test_decide_matrix(quote, plan, budget, decision):
    assert decide(quote, plan, EXPECT, budget)[0] is decision


def test_decide_names_each_mismatch_and_ignores_unrequested_fields():
    _, wrong = decide(Quote(2, "video", 20), Plan("Veo 3.1 - Fast", 6), EXPECT, 50)
    assert wrong == ["count: expected 1, quoted 2", "model: expected 'Veo 3.1 - Lite', planned "
                     "'Veo 3.1 - Fast'", "length: expected 8 s, planned 6 s"]
    loose = Expect(kind="video", count=1, model=None, seconds=None)
    assert decide(QUOTE, Plan(None, None), loose, 10)[0] is Decision.APPROVE


def test_instruction_has_no_newlines_since_enter_submits():
    text = instruction("video", "para one\n\npara two\r\nthree", count=1, model_label=None,
                       seconds=None, aspect=None)
    assert "\n" not in text and "\r" not in text
    assert text.endswith("Prompt: para one para two three")


def test_priced_actions_need_a_budget_that_covers_them():
    from aistudio._flow.models import check_direct_spend
    from aistudio.exceptions import OverBudgetError

    assert check_direct_spend("character", "Nano Banana 2.1", None) == 0
    assert check_direct_spend("extend", "Veo 3.1 - Lite", 10) == 10
    with pytest.raises(OverBudgetError) as info:
        check_direct_spend("extend", "Veo 3.1 - Lite", None)
    assert "--max-credits 10" in info.value.hint
    with pytest.raises(OverBudgetError):
        check_direct_spend("extend", "Veo 3.1 - Lite", 9)


def test_unpriced_actions_are_refused():
    from aistudio._flow.models import check_direct_spend

    with pytest.raises(ValidationError) as info:
        check_direct_spend("extend", "Veo 3.1 - Fast", 100)
    assert "price" in info.value.message



def test_measured_prices():
    # Plan G: the user-approved measurements (findings, "Flow paid measurements").
    from aistudio._flow.models import check_direct_spend
    from aistudio.exceptions import OverBudgetError

    assert check_direct_spend("character", "Nano Banana Pro", None) == 0
    assert check_direct_spend("body", "Nano Banana 2.1", None) == 0
    assert check_direct_spend("edit", "Omni 1.1 Flash", 20) == 20
    with pytest.raises(OverBudgetError):
        check_direct_spend("edit", "Omni 1.1 Flash", None)
    for action, label in (("character", "Nano Banana 2 Lite"), ("body", "Nano Banana Pro")):
        with pytest.raises(ValidationError):
            check_direct_spend(action, label, 100)
