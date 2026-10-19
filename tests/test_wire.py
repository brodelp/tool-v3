"""Wire decoder: exact numbers, strict UTF-8/JSON, BOM, duplicates, depth (V03, V09, V11, V13)."""

import pytest

from scat_validator.wire import (
    DecodeFailure,
    JsonNumber,
    decode_document,
    exceeds_depth,
)


@pytest.mark.parametrize(
    "token",
    [
        "0",
        "-0",
        "9007199254740991",
        "-9007199254740991",
        "9007199254740991.0",
        "9007199254740991.000",
        "9.007199254740991e15",
        "900719925474099.1e1",
        "0.1",
        "0.100000000000000005",
        "1.0",
        "1e0",
        "1e-400",
        "-1e-400",
        "1.2300",
        "0e99999999999999999999999",
        "0.0e-99999999999999999999999",
        "1e-99999999999999999999999",
        "9007199254740990.9999999999999999999",
        "1E15",
        "-1e+15",
    ],
)
def test_safe_numbers(token: str) -> None:
    assert JsonNumber(token, is_integer_token="." not in token and "e" not in token.lower()).is_safe


@pytest.mark.parametrize(
    "token",
    [
        "9007199254740992",
        "-9007199254740992",
        "9007199254740993",
        "9007199254740993.0",
        "9007199254740991.0000000000000001",
        "9007199254740991.1",
        "1e400",
        "-1e400",
        "1e16",
        "9.007199254740992e15",
        "1e99999999999999999999999",
        "-1E+99999999999999999999999",
        "9" * 5000,
        "1" + "0" * 4400,
    ],
)
def test_unsafe_numbers(token: str) -> None:
    assert not JsonNumber(
        token, is_integer_token="." not in token and "e" not in token.lower()
    ).is_safe


def test_safe_int_only_for_integer_tokens() -> None:
    assert JsonNumber("42", is_integer_token=True).safe_int() == 42
    assert JsonNumber("-0", is_integer_token=True).safe_int() == 0
    assert JsonNumber("1.0", is_integer_token=False).safe_int() is None
    assert JsonNumber("9" * 5000, is_integer_token=True).safe_int() is None


def test_decoder_retains_exact_tokens_and_kinds() -> None:
    raw = b'{"a": 0.100000000000000005, "b": 1e-400, "c": -0, "d": 1.2300, "e": true, "f": "1"}'
    value = decode_document(raw).value
    assert isinstance(value, dict)
    tokens = {
        k: (v.token, v.is_integer_token) for k, v in value.items() if isinstance(v, JsonNumber)
    }
    assert tokens == {
        "a": ("0.100000000000000005", False),
        "b": ("1e-400", False),
        "c": ("-0", True),
        "d": ("1.2300", False),
    }
    assert value["e"] is True
    assert value["f"] == "1"


def test_huge_integer_token_decodes_without_digit_limit_error() -> None:
    value = decode_document(b'{"n": ' + b"7" * 5000 + b"}").value
    assert isinstance(value, dict)
    number = value["n"]
    assert isinstance(number, JsonNumber) and not number.is_safe


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"   ",
        b"{",
        b"{} {}",
        b"{} x",
        b'{"a": 1,}',
        b"{'a': 1}",
        b'{"a": 01}',
        b'{"a": 1.}',
        b'{"a": .5}',
        b'{"a": +1}',
        b'{"a": NaN}',
        b'{"a": Infinity}',
        b'{"a": -Infinity}',
        b'{"a": 1} // comment',
        b'/* c */ {"a": 1}',
        b'{"a": "\x01"}',
        b'{"a": "x\xff"}',
        b'{"a": "\xed\xa0\x80"}',  # UTF-8-encoded surrogate is malformed UTF-8
        b'{"a": "\xc0\xaf"}',  # overlong encoding
        b"\xef\xbb\xbf\xef\xbb\xbf{}",
        b"{}\xef\xbb\xbf",
        b'{"a":\xef\xbb\xbf 1}',
        b"\xfe\xff{}",
    ],
)
def test_invalid_json(raw: bytes) -> None:
    with pytest.raises(DecodeFailure) as caught:
        decode_document(raw)
    assert caught.value.code == "INVALID_JSON"


def test_single_leading_bom_is_ignored_and_feff_in_strings_is_content() -> None:
    document = decode_document(b'\xef\xbb\xbf {"a": "\xef\xbb\xbfx\\ufeff"} \n')
    assert document.value == {"a": "﻿x﻿"}
    assert document.document_bytes == b'{"a": "\xef\xbb\xbfx\\ufeff"}'


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a": 1, "a": 2}',
        b'{"a": 1, "\\u0061": 2}',
        b'{"\\u00e9": 1, "\xc3\xa9": 2}',
        b'{"x": [{"k": 1, "k": 1}]}',
        b'{"\\ud83d\\ude00": 1, "\xf0\x9f\x98\x80": 2}',
    ],
)
def test_duplicate_decoded_names(raw: bytes) -> None:
    with pytest.raises(DecodeFailure) as caught:
        decode_document(raw)
    assert caught.value.code == "DUPLICATE_PROPERTY"


def test_same_name_in_separate_objects_is_valid() -> None:
    assert decode_document(b'{"a": {"k": 1}, "b": {"k": 2}, "k": [{"k": 3}]}').value is not None


def test_syntax_error_takes_precedence_over_duplicate_name() -> None:
    with pytest.raises(DecodeFailure) as caught:
        decode_document(b'{"a": 1, "a": 2} trailing')
    assert caught.value.code == "INVALID_JSON"


def nested(depth: int, inner: str = "") -> str:
    return "[" * depth + inner + "]" * depth


def test_depth_boundaries() -> None:
    assert not exceeds_depth(nested(128))
    assert exceeds_depth(nested(129))
    assert not exceeds_depth('{"a":' * 127 + "[]" + "}" * 127)
    assert exceeds_depth('{"a":' * 128 + "[]" + "}" * 128)
    assert not exceeds_depth("1")


def test_brackets_and_escaped_quotes_inside_strings_do_not_count() -> None:
    text = '[["' + "[{" * 300 + '\\"]]]\\\\", "\\"[[[["]]'
    assert not exceeds_depth(text)
    assert decode_document(text.encode()).value is not None


def test_depth_failure_precedes_json_errors() -> None:
    with pytest.raises(DecodeFailure) as caught:
        decode_document(nested(129, "garbage").encode())
    assert caught.value.code == "LIMIT_EXCEEDED"


def test_unterminated_string_does_not_inflate_depth() -> None:
    with pytest.raises(DecodeFailure) as caught:
        decode_document(b'["' + b"[" * 500)
    assert caught.value.code == "INVALID_JSON"


def test_lone_surrogate_escape_decodes_for_later_policy_check() -> None:
    assert decode_document(b'{"a": "\\ud800"}').value == {"a": "\ud800"}
    assert decode_document(b'{"a": "\\ud83d\\ude00"}').value == {"a": "\U0001f600"}
