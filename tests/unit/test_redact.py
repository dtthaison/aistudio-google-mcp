import json

from aistudio._dev.redact import REDACTED_KEY, RawExchange, Redactor

KEY = "AIza" + "S" * 35
DRIVE_A = "1" + "a" * 32
DRIVE_B = "1" + "b" * 32
RPC = (
    "https://alkalimakersuite-pa.clients6.google.com/$rpc/"
    "google.internal.alkali.applications.makersuite.v1.MakerSuiteService/"
)


def exchange(**overrides):
    base = dict(
        url=RPC + "GetPrompt",
        http_method="POST",
        request_headers={"Content-Type": "application/json+protobuf"},
        request_body=b"[]",
        status=200,
        response_headers={"content-type": "application/json+protobuf"},
        response_body=b"[]",
    )
    base.update(overrides)
    return RawExchange(**base)


def test_drops_sensitive_headers_case_insensitively():
    record = Redactor().exchange(exchange(
        request_headers={
            "Cookie": "SID=secret", "AUTHORIZATION": "SAPISIDHASH 1_abc",
            "X-Goog-Api-Key": KEY, "x-goog-authuser": "1",
            "Content-Type": "application/json+protobuf",
        },
        response_headers={"Set-Cookie": "SID=new", "Content-Type": "application/json"},
    ))
    assert record["request"]["headers"] == {"content-type": "application/json+protobuf"}
    assert record["response"]["headers"] == {"content-type": "application/json"}


def test_redacts_api_key_in_url_and_body():
    record = Redactor().exchange(exchange(
        url=RPC + f"ListModels?key={KEY}&alt=json",
        request_body=f'["{KEY}"]'.encode(),
    ))
    assert record["url"] == RPC + f"ListModels?key={REDACTED_KEY}&alt=json"
    assert KEY not in json.dumps(record)


def test_redacts_email_and_authuser():
    record = Redactor().exchange(exchange(
        url=RPC + "GetPrompt?authuser=3",
        response_body=b'[["owner","someone.real@gmail.com"]]',
    ))
    assert record["url"].endswith("authuser=0")
    assert record["response"]["body"] == '[["owner","user@example.com"]]'


def test_drive_ids_map_to_stable_fakes_across_exchanges():
    redactor = Redactor()
    first = redactor.exchange(exchange(request_body=f'["{DRIVE_A}"]'.encode()))
    second = redactor.exchange(exchange(
        url=RPC + f"GetPrompt?id={DRIVE_A}",
        response_body=f'[["{DRIVE_B}","{DRIVE_A}"]]'.encode(),
    ))
    assert first["request"]["body"] == '["prompt_1"]'
    assert second["url"].endswith("id=prompt_1")
    assert second["response"]["body"] == '[["prompt_2","prompt_1"]]'


def test_binary_body_is_base64():
    record = Redactor().exchange(exchange(response_body=b"\xff\xd8\xff\xe0"))
    assert record["response"] == {
        "status": 200, "headers": {"content-type": "application/json+protobuf"},
        "body": "/9j/4A==", "body_encoding": "base64",
    }


def test_record_shape_and_method_name():
    record = Redactor().exchange(exchange())
    assert record["method"] == "GetPrompt"
    assert record["http_method"] == "POST"
    assert record["request"]["body"] == "[]"
    assert record["response"]["status"] == 200


def test_full_record_contains_no_secrets():
    secrets = ["SID=secret", "SAPISIDHASH 1_abc", KEY, "someone.real@gmail.com", DRIVE_A]
    record = Redactor().exchange(exchange(
        url=RPC + f"GetPrompt?key={KEY}",
        request_headers={"cookie": "SID=secret", "authorization": "SAPISIDHASH 1_abc"},
        request_body=f'["{DRIVE_A}","someone.real@gmail.com"]'.encode(),
        response_body=f'["{KEY}"]'.encode(),
    ))
    dumped = json.dumps(record)
    for secret in secrets:
        assert secret not in dumped
