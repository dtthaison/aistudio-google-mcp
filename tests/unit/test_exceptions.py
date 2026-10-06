import pytest

from aistudio.exceptions import (
    AuthError,
    NotFoundError,
    QuotaExceededError,
    RateLimitError,
    SchemaDriftError,
    ServerError,
    ValidationError,
    error_from_http,
    error_from_status,
)


@pytest.mark.parametrize(
    "status,message,cls,retriable",
    [
        (3, "bad arg", ValidationError, False),
        (5, "no such prompt", NotFoundError, False),
        (8, "Resource has been exhausted", RateLimitError, True),
        (8, "Quota exceeded for model", QuotaExceededError, False),
        (7, "The caller does not have permission", AuthError, False),
        (7, "Quota exceeded", QuotaExceededError, False),
        (16, "unauthenticated", AuthError, False),
        (13, "Internal error encountered.", ServerError, True),
        (14, "unavailable", ServerError, True),
        (99, "weird", ServerError, False),
    ],
)
def test_error_from_status(status, message, cls, retriable):
    err = error_from_status(status, message)
    assert type(err) is cls
    assert err.retriable is retriable


def test_auth_errors_carry_login_hint():
    assert error_from_status(16, "x").hint == 'run "aistudio login"'
    assert error_from_http(401, "").hint == 'run "aistudio login"'


@pytest.mark.parametrize(
    "status,cls",
    [(401, AuthError), (403, AuthError), (404, NotFoundError), (429, RateLimitError),
     (503, ServerError), (400, ValidationError)],
)
def test_error_from_http(status, cls):
    assert type(error_from_http(status, "<html>")) is cls


def test_error_from_http_truncates_body():
    err = error_from_http(500, "x" * 1000)
    assert len(err.message) < 250


def test_to_dict_envelope():
    err = NotFoundError("gone", hint="check the id")
    assert err.to_dict() == {
        "error": True, "code": "NOT_FOUND", "message": "gone", "hint": "check the id",
    }


def test_to_dict_omits_missing_hint():
    assert "hint" not in ValidationError("bad").to_dict()


def test_schema_drift_names_method_and_path():
    err = SchemaDriftError("ListPrompts", (0, 2, 1), "a string")
    assert err.code == "SCHEMA_DRIFT"
    assert "ListPrompts" in err.message
    assert "[0][2][1]" in err.message
    assert err.hint == 're-capture ListPrompts with "aistudio dev capture"'
