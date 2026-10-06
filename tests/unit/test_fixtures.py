import json

import pytest

from aistudio._dev.fixtures import FixtureWriter, iter_fixtures, load_fixture, rpc_method_name
from aistudio.exceptions import ValidationError

RPC = (
    "https://alkalimakersuite-pa.clients6.google.com/$rpc/"
    "google.internal.alkali.applications.makersuite.v1.MakerSuiteService/"
)


def record(method):
    return {"method": method, "url": RPC + method, "http_method": "POST",
            "request": {"headers": {}, "body": "[]"},
            "response": {"status": 200, "headers": {}, "body": "[]"}}


def test_rpc_method_name():
    assert rpc_method_name(RPC + "ListPrompts") == "ListPrompts"
    assert rpc_method_name(RPC + "ListPrompts?alt=json") == "ListPrompts"
    assert rpc_method_name("https://content-push.googleapis.com/upload/v1") == (
        "other_content_push_googleapis_com_upload_v1"
    )


def test_writer_numbers_per_method(tmp_path):
    writer = FixtureWriter(tmp_path)
    assert writer.write(record("ListModels")) == tmp_path / "ListModels" / "1.json"
    assert writer.write(record("ListModels")) == tmp_path / "ListModels" / "2.json"
    assert writer.write(record("GetPrompt")) == tmp_path / "GetPrompt" / "1.json"
    assert json.loads((tmp_path / "GetPrompt" / "1.json").read_text())["method"] == "GetPrompt"


def test_writer_continues_after_existing_files(tmp_path):
    (tmp_path / "ListModels").mkdir()
    (tmp_path / "ListModels" / "3.json").write_text("{}")
    assert FixtureWriter(tmp_path).write(record("ListModels")).name == "4.json"


def test_load_fixture_validates(tmp_path):
    good = FixtureWriter(tmp_path).write(record("ListModels"))
    assert load_fixture(good)["method"] == "ListModels"
    bad = tmp_path / "bad.json"
    bad.write_text('{"nope": 1}')
    with pytest.raises(ValidationError):
        load_fixture(bad)


def test_iter_fixtures_sorts_numerically(tmp_path):
    writer = FixtureWriter(tmp_path)
    for _ in range(10):
        writer.write(record("ListModels"))
    writer.write(record("GetPrompt"))
    names = [f"{p.parent.name}/{p.name}" for p in iter_fixtures(tmp_path)]
    assert names[0] == "GetPrompt/1.json"
    assert names[1:3] == ["ListModels/1.json", "ListModels/2.json"]
    assert names[-1] == "ListModels/10.json"
