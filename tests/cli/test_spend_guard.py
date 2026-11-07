"""A command spends credits only when the user passes --max-credits (CLAUDE.md, rule table).

Every command with a budget option must leave it unset by default and never require it: unset
means "quote only". A default would make a plain run spend, so this test walks the whole CLI.
"""

import click
from click.testing import CliRunner

from aistudio.cli.main import cli

BUDGET_OPTIONS = {"--max-credits", "--max-cost"}
NO_DEFAULT = getattr(click.core, "UNSET", None)  # click 8.3 marks "no default" with UNSET


def commands(group: click.Group, path: str = "aistudio"):
    for name, command in group.commands.items():
        here = f"{path} {name}"
        if isinstance(command, click.Group):
            yield from commands(command, here)
        else:
            yield here, command


def budget_problems(group: click.Group) -> list[str]:
    problems = []
    for path, command in commands(group):
        for param in command.params:
            if not isinstance(param, click.Option) or not BUDGET_OPTIONS & set(param.opts):
                continue
            if param.default is not None and param.default is not NO_DEFAULT:
                problems.append(f"{path}: {param.opts[0]} defaults to {param.default!r}")
            if param.required:
                problems.append(f"{path}: {param.opts[0]} is required")
    return problems


def test_no_budget_option_has_a_default():
    assert budget_problems(cli) == []


def test_the_paid_commands_have_a_budget_option():
    names = {path for path, command in commands(cli)
             if any(BUDGET_OPTIONS & set(p.opts) for p in command.params
                    if isinstance(p, click.Option))}
    assert {"aistudio flow video", "aistudio flow image", "aistudio flow extend",
            "aistudio flow edit", "aistudio flow storyboard"} <= names


def test_a_planted_default_is_caught():
    @click.group()
    def fake() -> None:
        pass

    @fake.command("spend")
    @click.option("--max-credits", type=int, default=10)
    def spend(max_credits: int) -> None:
        pass

    assert budget_problems(fake) == ["aistudio spend: --max-credits defaults to 10"]


def test_the_batch_option_comes_with_the_budget_option_and_no_default():
    image = next(c for path, c in commands(cli) if path == "aistudio flow image")
    names = {opt for p in image.params if isinstance(p, click.Option) for opt in p.opts}
    assert {"--prompts-file", "--max-credits"} <= names


def test_a_batch_without_max_credits_never_gets_a_budget(logged_in_profile, monkeypatch, tmp_path):
    # --prompts-file must not turn a quote-only run into a spending one
    logged_in_profile()
    seen = []

    async def batch(profile, prompts, **kwargs):
        seen.append(kwargs["max_credits"])
        return {"status": "done", "total": 1, "start_at": 1, "results": [], "credits_spent": 0}

    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_image_batch", batch)
    prompts = tmp_path / "p.jsonl"
    prompts.write_text('"an apple"\n')
    out = tmp_path / "out"
    out.mkdir()
    result = CliRunner().invoke(cli, ["flow", "image", "--prompts-file", str(prompts), "-o",
                                      str(out), "--json"])
    assert result.exit_code == 0, result.output
    assert seen == [None]
