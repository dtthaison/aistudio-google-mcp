"""Flow models and options, the instruction for its agent, and the spend decision (spec §3)."""

from __future__ import annotations

import enum
from dataclasses import dataclass

from aistudio._flow.rows import Plan, Quote
from aistudio.exceptions import OverBudgetError, ValidationError

ASPECTS = {"video": ("16:9", "9:16"), "image": ("16:9", "4:3", "1:1", "3:4", "9:16")}
COUNTS = (1, 2, 3, 4)


@dataclass(frozen=True)
class FlowModel:
    id: str
    label: str  # as Flow's model picker shows it
    kind: str  # "video" | "image"
    durations: tuple[int, ...] = ()


MODELS = (
    FlowModel("omni-1.1-flash", "Omni 1.1 Flash", "video", (4, 6, 8, 10)),
    FlowModel("veo-3.1-lite", "Veo 3.1 - Lite", "video", (4, 6, 8)),
    FlowModel("veo-3.1-fast", "Veo 3.1 - Fast", "video", (4, 6, 8)),
    FlowModel("veo-3.1-quality", "Veo 3.1 - Quality", "video", (8,)),
    FlowModel("nano-banana-2.1", "Nano Banana 2.1", "image"),
    FlowModel("nano-banana-pro", "Nano Banana Pro", "image"),
)


def find_model(model_id: str, kind: str) -> FlowModel:
    wanted = model_id.strip().lower()
    for model in MODELS:
        if model.id == wanted:
            if model.kind != kind:
                raise ValidationError(f"{model.id} is a {model.kind} model",
                                      hint=f'use "aistudio flow {model.kind}"')
            return model
    ids = ", ".join(m.id for m in MODELS if m.kind == kind)
    raise ValidationError(f"Flow has no {kind} model {model_id!r}", hint=f"choose one of: {ids}")


def check_options(kind: str, model: FlowModel | None, aspect: str | None, count: int | None,
                  duration: int | None) -> None:
    if aspect is not None and aspect not in ASPECTS[kind]:
        raise ValidationError(f"Flow {kind}s come in {', '.join(ASPECTS[kind])}, not {aspect}")
    if count is not None and count not in COUNTS:
        raise ValidationError("--count must be 1 to 4")
    if duration is not None:
        if kind == "image":
            raise ValidationError("images have no --duration")
        allowed = model.durations if model else (4, 6, 8, 10)
        if duration not in allowed:
            name = model.id if model else "Flow"
            raise ValidationError(f"{name} makes {', '.join(map(str, allowed))}-second videos, "
                                  f"not {duration}")


def instruction(kind: str, prompt: str, *, count: int, model_label: str | None,
                seconds: int | None, aspect: str | None) -> str:
    prompt = " ".join(prompt.split())  # typing a newline presses Enter, which submits
    noun = kind if count == 1 else f"{kind}s"
    length = f"{seconds}-second " if seconds else ""
    model = f" with {model_label}" if model_label else ""
    shape = f" in {aspect}" if aspect else ""
    return (f"Generate exactly {count} {length}{noun}{model}{shape}. Do not make a storyboard "
            f"or any other {kind}s. Prompt: {prompt}")


class Decision(enum.Enum):
    QUOTE_ONLY = "quote_only"
    APPROVE = "approve"
    REJECT_OVER_BUDGET = "reject_over_budget"
    REJECT_MISMATCH = "reject_mismatch"


@dataclass(frozen=True)
class Expect:
    kind: str
    count: int
    model: str | None  # a label; None when the person did not ask for one
    seconds: int | None


def decide(quote: Quote, plan: Plan, expect: Expect,
           max_credits: int | None) -> tuple[Decision, list[str]]:
    """Spend only with a budget, a matching plan and a quote within the budget (spec §3.1)."""
    wrong: list[str] = []
    if quote.kind != expect.kind:
        wrong.append(f"kind: expected {expect.kind}, quoted {quote.kind}")
    if quote.count != expect.count:
        wrong.append(f"count: expected {expect.count}, quoted {quote.count}")
    # Flow's permission request carries only counts and cost (live probe): the model comes from
    # the settings saved and re-read before asking, and the length is checked after the job.
    # A plan the agent does state must still match.
    if expect.model is not None and plan.model is not None and (
            _norm(plan.model) != _norm(expect.model)):
        wrong.append(f"model: expected {expect.model!r}, planned {plan.model!r}")
    if expect.seconds is not None and plan.seconds is not None and plan.seconds != expect.seconds:
        wrong.append(f"length: expected {expect.seconds} s, planned {plan.seconds} s")
    if max_credits is None:
        return Decision.QUOTE_ONLY, wrong
    if wrong:
        return Decision.REJECT_MISMATCH, wrong
    if quote.credits > max_credits:
        return Decision.REJECT_OVER_BUDGET, wrong
    return Decision.APPROVE, wrong


def _norm(label: str | None) -> str:
    return "".join(ch for ch in (label or "").lower() if ch.isalnum())


# Direct-spend actions: Flow charges without a quote, so aistudio keeps measured prices
# (docs/wire/findings.md, "measured costs"). Add a row only after measuring it.
PRICES = {("character", "Nano Banana 2.1"): 0, ("extend", "Veo 3.1 - Lite"): 10}


def check_direct_spend(action: str, model_label: str, max_credits: int | None) -> int:
    price = PRICES.get((action, model_label))
    if price is None:
        raise ValidationError(f"aistudio has no measured price for {action} with {model_label}",
                              hint="Flow does not quote this action; it needs a measured price "
                                   "in aistudio's table before it can be used")
    if price > 0 and (max_credits is None or max_credits < price):
        raise OverBudgetError(f"{action} with {model_label} costs {price} credits",
                              hint=f"nothing was spent; pass --max-credits {price} to accept")
    return price
