import pytest

from aistudio._rpc import wire
from aistudio.exceptions import SchemaDriftError


def test_encode_is_compact_and_keeps_unicode():
    assert wire.encode([None, "héllo", [1, True]]) == '[null,"héllo",[1,true]]'


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('[,[13,"x"]]', '[null,[13,"x"]]'),
        ("[1,,2]", "[1,null,2]"),
        ("[1,]", "[1]"),
        ("[,]", "[null]"),
        ("[1,,]", "[1,null]"),
        ("[[,],]", "[[null]]"),
        ("[1, ]", "[1 ]"),
        ("[]", "[]"),
        ('{"a":[,1]}', '{"a":[null,1]}'),
    ],
)
def test_normalize_elisions(raw, expected):
    assert wire.normalize_elisions(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [r'["a,,b","[,"]', r'["q\",,",1]', r'["\\",2]', r'["]",3]'],
)
def test_normalize_leaves_string_contents_alone(raw):
    assert wire.normalize_elisions(raw) == raw


def test_decode_strips_xssi_prefix():
    assert wire.decode(")]}'\n[1,[2]]") == [1, [2]]


def test_decode_empty_body_is_none():
    assert wire.decode("") is None
    assert wire.decode("  \n") is None


def test_decode_google_error_envelope():
    assert wire.decode('[,[13,"Internal error encountered."]]') == [
        None, [13, "Internal error encountered."],
    ]


def test_decode_invalid_raises_value_error():
    with pytest.raises(ValueError):
        wire.decode("<html>")


def test_parse_error_shapes():
    assert wire.parse_error([None, [7, "denied"]]) == (7, "denied")
    assert wire.parse_error([5, "not found", []]) == (5, "not found")
    assert wire.parse_error([["models/a", None]]) is None
    assert wire.parse_error([True, "x"]) is None
    assert wire.parse_error(None) is None


def test_safe_index_returns_value_including_none():
    row = [None, ["a", [None, "deep"]]]
    assert wire.safe_index(row, 1, 1, 1, method="M", expected="str") == "deep"
    assert wire.safe_index(row, 0, method="M", expected="anything") is None


def test_safe_index_raises_schema_drift_with_path():
    with pytest.raises(SchemaDriftError) as info:
        wire.safe_index([None, ["a"]], 1, 3, method="ListPrompts", expected="a title")
    assert info.value.index_path == (1, 3)
    assert info.value.method == "ListPrompts"


def test_safe_index_through_non_list_raises():
    with pytest.raises(SchemaDriftError):
        wire.safe_index([None, "str"], 1, 0, method="M", expected="list")


@pytest.mark.parametrize("body", ["[NaN]", "[1,Infinity]", "[-Infinity]"])
def test_decode_rejects_nan_and_infinity(body):
    with pytest.raises(ValueError):
        wire.decode(body)
