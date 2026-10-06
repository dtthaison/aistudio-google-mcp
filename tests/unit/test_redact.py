import base64
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


ACCESS_TOKEN = "ya29.a0AfH6SMBx-y_z.123abc"
REFRESH_TOKEN = "1//0g" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 3 + "-xyz_123"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnX3BhcnQtMQ"


def test_redacts_oauth_tokens_and_jwts():
    record = Redactor().exchange(exchange(
        url=RPC + "GenerateAccessToken?access_token=abc123&upload_id=UP1&sig=s1&alt=json",
        response_body=f'[["{ACCESS_TOKEN}",3599],["{REFRESH_TOKEN}"],["{JWT}"]]'.encode(),
    ))
    dumped = json.dumps(record)
    for secret in (ACCESS_TOKEN, REFRESH_TOKEN, JWT, "abc123", "UP1", "sig=s1"):
        assert secret not in dumped
    assert record["url"].endswith(
        "?access_token=REDACTED&upload_id=REDACTED&sig=REDACTED&alt=json"
    )


def test_binary_body_with_personal_data_is_withheld():
    raw = b"\x0a\x10someone.real@gmail.com\xff\xfe" + KEY.encode()
    record = Redactor().exchange(exchange(request_body=raw))
    assert record["request"]["body"] == ""
    assert record["request"]["body_encoding"] == "withheld"
    assert "someone.real" not in json.dumps(record)


def test_method_and_directory_name_never_carry_raw_ids_or_emails():
    redactor = Redactor()
    drive = redactor.exchange(exchange(url=f"https://www.googleapis.com/drive/v3/files/{DRIVE_A}"))
    assert drive["method"] == "other_www_googleapis_com_drive"
    person = redactor.exchange(exchange(url="https://x.googleapis.com/users/jane.doe@gmail.com/x"))
    assert "jane" not in person["method"]


def test_redacts_percent_and_unicode_escaped_emails():
    record = Redactor().exchange(exchange(
        url=RPC + "GetPrompt?login_hint=jane.doe%40gmail.com",
        response_body=b'["jane.doe\\u0040gmail.com"]',
    ))
    assert "jane.doe" not in json.dumps(record)


def test_drops_upload_session_and_client_headers():
    record = Redactor().exchange(exchange(response_headers={
        "X-Goog-Upload-URL": "https://x.googleapis.com/upload?upload_id=abc",
        "X-GUploader-UploadID": "uploader-id",
        "X-Client-Data": "CIa2yQEIpbbJAQ==",
        "Content-Type": "application/json",
    }))
    assert record["response"]["headers"] == {"content-type": "application/json"}


def test_redacts_auth_headers_embedded_in_batch_bodies():
    body = (
        b"--batch_x\r\nContent-Type: application/http\r\n\r\nGET /v2/people HTTP/1.1\r\n"
        b"X-Goog-AuthUser: 0\r\nAuthorization: SAPISIDHASH 123_abc SAPISID1PHASH 123_def\r\n"
        b"Cookie: SID=x\r\nX-Goog-Api-Key: k\r\n\r\n--batch_x--"
    )
    record = Redactor().exchange(exchange(request_body=body))
    text = record["request"]["body"]
    for secret in ("123_abc", "123_def", "SID=x", "X-Goog-Api-Key: k"):
        assert secret not in text
    assert "Authorization: REDACTED\r\n" in text
    assert "GET /v2/people HTTP/1.1" in text


def test_redacts_profile_photos_and_account_ids_stably():
    photo = "https://lh3.googleusercontent.com/a/ACg8ocJVNKhWboK-lq_v3=s96-c"
    gaia = "113629000000000000001"
    record = Redactor().exchange(exchange(
        response_body=f'[["{photo}","{gaia}"],["{gaia}","113629000000000000002"]]'.encode()
    ))
    body = record["response"]["body"]
    assert "ACg8oc" not in body and gaia not in body
    assert "https://lh3.googleusercontent.com/a/REDACTED" in body
    assert body.count("100000000000000000001") == 2
    assert "100000000000000000002" in body


def test_redacts_extra_literal_strings_case_insensitively():
    record = Redactor(extra=("Test Person",)).exchange(exchange(
        response_body=b'[["Test Person",1],["TEST PERSON"]]'
    ))
    assert record["response"]["body"] == '[["Redacted Name",1],["Redacted Name"]]'


def test_redact_record_rewrites_an_existing_fixture():
    raw = exchange(
        url=RPC + "GetPrompt",
        request_body=b"Authorization: SAPISIDHASH 1_secret\r\n",
        response_body=b'["Test Person","113629000000000000001"]',
    )
    first = Redactor().exchange(raw)
    again = Redactor(extra=("Test Person",)).record(first)
    dumped = json.dumps(again)
    assert "1_secret" not in dumped
    assert "Test Person" not in dumped
    assert again["method"] == "GetPrompt"
    assert Redactor(extra=("Test Person",)).record(again) == again


def test_base64_media_survives_redaction_byte_for_byte():
    media = base64.b64encode(bytes(range(256)) * 40).decode()
    # Slash-delimited runs inside standard base64 look like Drive ids and refresh tokens.
    media = media[:400] + "/1" + "A" * 30 + "/1//0" + "B" * 60 + "+" + media[400:]
    body = f'[["{DRIVE_A}","{media}"]]'
    record = Redactor().exchange(exchange(request_body=body.encode()))
    assert media in record["request"]["body"]
    assert DRIVE_A not in record["request"]["body"]


def test_redacts_cloud_project_numbers_and_ids():
    record = Redactor().exchange(exchange(response_body=(
        b'[["projects/1234567890123","gen-lang-client-0123456789"],["projects/my-app-42"]]'
    )))
    body = record["response"]["body"]
    assert "1234567890123" not in body and "0123456789" not in body and "my-app-42" not in body
    assert "projects/000000000000" in body and "gen-lang-client-0000000000" in body
