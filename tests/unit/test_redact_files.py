import json

from aistudio._dev.fixtures import FixtureWriter
from aistudio._dev.redact import Redactor, redact_fixtures


def test_redact_fixtures_rewrites_files_in_place(tmp_path):
    writer = FixtureWriter(tmp_path)
    leaky = writer.write({
        "method": "GetPrompt", "url": "https://x.google.com/$rpc/S/GetPrompt",
        "http_method": "POST",
        "request": {"headers": {}, "body": "Authorization: SAPISIDHASH 1_secret\r\n"},
        "response": {"status": 200, "headers": {}, "body": '["Test Person"]'},
    })
    clean = writer.write({
        "method": "GetPrompt", "url": "https://x.google.com/$rpc/S/GetPrompt",
        "http_method": "POST",
        "request": {"headers": {}, "body": "[]"},
        "response": {"status": 200, "headers": {}, "body": "[]"},
    })
    assert redact_fixtures(tmp_path, Redactor(extra=("Test Person",))) == (2, 1)
    text = leaky.read_text()
    assert "1_secret" not in text and "Test Person" not in text
    assert json.loads(clean.read_text())["request"]["body"] == "[]"
