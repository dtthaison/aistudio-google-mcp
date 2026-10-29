import pytest

from aistudio._rpc.rows.build import (
    decode_arena_options,
    decode_build_stream,
    decode_created_app,
    decode_started_run,
)
from aistudio._types.apps import BuildProgress
from aistudio.exceptions import SchemaDriftError

APP = "912435b3-26c1-41b4-ba22-058d464afceb"
RUN = "463f35e4-afb1-41fc-925f-8d550c1d0ffc"
FILES = ("index.html", "src/main.tsx", "package.json", "src/App.tsx", ".gitignore",
         "src/index.css", "vite.config.ts", "metadata.json", ".env.example", "tsconfig.json")


def test_created_app_id(captured):
    assert decode_created_app(captured("CreateApplet", 2).response) == APP
    with pytest.raises(SchemaDriftError):
        decode_created_app([])


def test_started_runs(captured):
    assert decode_started_run(captured("CodeAssistantOffline", 2).response) == (RUN, APP)
    assert decode_started_run(captured("CodeAssistantOffline", 3).response) == (
        "66ca59ad-237a-4446-b206-eca5cabac1c7", APP)
    with pytest.raises(SchemaDriftError):
        decode_started_run(["only-run"])


def test_completed_stream(captured):
    assert decode_build_stream(captured("StreamCodeAssistantOfflineGeneration").response) == (
        BuildProgress(run_id=RUN, app_id=APP, steps=24, done=True, files=FILES,
                      summary="Snapshot from A simple counter web page with one big button "
                              "that increments a number.")
    )


def test_unfinished_stream_is_not_done(captured):
    response = captured("StreamCodeAssistantOfflineGeneration").response
    partial = [response[0][:-1]]
    progress = decode_build_stream(partial)
    assert (progress.run_id, progress.done, progress.steps, progress.files) == (RUN, False, 23, ())


@pytest.mark.parametrize("response", [None, [], [None], ["junk"], [[["junk"]]]])
def test_empty_or_odd_streams_decode_as_not_done(response):
    assert decode_build_stream(response) == BuildProgress(None, None, 0, False)


# GetPendingArenaInteraction for app a6af8f55… (live 2026-10-29): two hidden option apps, each
# with its own run.
ARENA = [1, [["f3b70e01-1598-4d18-9a6e-72132dda3e72", "e96502c5-7226-4dcb-9cff-15d494e9ce7f",
              "0b6487e4-a83d-4bbd-b591-7924b33ffa06"],
             ["79f6eed8-5ad7-43fe-9358-7a253c3d357a", "7dd040de-6ee2-43bb-b808-33b8cc24921a",
              "c173307c-8cae-4ef1-8963-271c438188ab"]]]


def test_arena_options_are_option_apps_with_their_runs():
    assert decode_arena_options(ARENA) == (
        ("f3b70e01-1598-4d18-9a6e-72132dda3e72", "e96502c5-7226-4dcb-9cff-15d494e9ce7f"),
        ("79f6eed8-5ad7-43fe-9358-7a253c3d357a", "7dd040de-6ee2-43bb-b808-33b8cc24921a"),
    )


@pytest.mark.parametrize("response", [[], None, [1], [1, []], [1, [["only-app"]]]])
def test_no_pending_choice_decodes_as_no_options(response):
    assert decode_arena_options(response) == ()


def step(n, run, final=None):
    entry = [None] * 17
    entry[8], entry[11], entry[16] = final, n, run
    return entry


def test_a_snapshot_carrying_every_step_is_read_to_its_last():
    # Live 2026-10-29 (an option's stream): one snapshot whose [2] lists steps 0..26, the
    # final model message on the last; earlier streams sent one step per snapshot.
    final = ["model", "Built it", None, ["1793265883", 0],
             [["index.html", "ab"], ["src/App.tsx", "cd"]]]
    response = [[[None, None, [step(0, RUN), step(1, RUN), step(26, RUN, final)], APP]]]
    assert decode_build_stream(response) == BuildProgress(
        RUN, APP, 26, True, ("index.html", "src/App.tsx"), "Built it")
