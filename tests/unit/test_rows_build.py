import pytest

from aistudio._rpc.rows.build import decode_build_stream, decode_created_app, decode_started_run
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
