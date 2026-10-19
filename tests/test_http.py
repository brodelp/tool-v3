"""HTTP contract: examples, preservation, transport, gates, echo, repeatability.

Covers V01, V08, V09, V11, V12, V13, V14, V21, V22 through raw ASGI requests.
"""

import json
import re
from typing import Any

import pytest

from scat_validator.config import MAX_BODY_BYTES
from scat_validator.openapi_docs import (
    EXAMPLE_INVALID_BATCH_REPORT,
    EXAMPLE_SINGLE_REPORT,
)
from tests.helpers import (
    JSON_HEADERS,
    RawResponse,
    call,
    dumps,
    example_bytes,
    issues_of,
    make_record,
    post_batch,
    post_single,
    raw_fixture,
)

FAILURE_KEYS = {
    "valid",
    "mode",
    "error",
    "issues",
    "issue_count",
    "omitted_issue_count",
    "checks_not_performed",
}


def assert_request_failure(response: RawResponse, status: int, code: str, issues: int) -> Any:
    assert response.status == status
    body = response.json()
    assert set(body) == FAILURE_KEYS
    assert body["valid"] is False
    assert body["error"]["code"] == code
    assert body["issue_count"] == issues == len(body["issues"])
    assert body["omitted_issue_count"] == 0
    assert response.header("cache-control") == "no-store"
    assert response.header("content-type") == "application/json"
    return body


def echo_bytes(response: RawResponse) -> bytes:
    """The raw ``echo`` member bytes, located without float-based JSON parsing."""
    marker = b',"echo":'
    position = response.body.rindex(marker)
    assert response.body.endswith(b"}")
    return response.body[position + len(marker) : -1]


# ---------------------------------------------------------------- V01


def test_valid_single_report_matches_spec_example() -> None:
    response = post_single(example_bytes("valid-record.json"))
    assert response.status == 200
    assert response.json() == EXAMPLE_SINGLE_REPORT
    assert response.header("cache-control") == "no-store"
    assert response.header("content-type") == "application/json"
    assert b'"echo"' not in response.body.replace(b'"echo":false', b"")


def test_invalid_batch_matches_spec_example_and_never_echoes() -> None:
    for query in ("", "echo=true"):
        response = post_batch(example_bytes("invalid-batch.json"), query=query)
        assert response.status == 422
        assert response.json() == EXAMPLE_INVALID_BATCH_REPORT
        assert "echo" not in response.json()


def test_valid_batch_and_repeated_pair_examples() -> None:
    response = post_batch(example_bytes("valid-batch.json"))
    assert response.status == 200
    assert response.json()["record_count"] == 3
    repeated = post_batch(example_bytes("repeated-case-pair-batch.json"))
    assert repeated.status == 422
    assert issues_of(repeated) == [("/records/1", "DUPLICATE_RECORD")]


def test_health() -> None:
    response = call("GET", "/healthz")
    assert response.status == 200
    assert response.body == b'{"status":"ok"}'


def test_compact_utf8_output_without_ascii_escaping() -> None:
    record = make_record(username="u", narrative="Café ☕ ok", annotations={})
    response = post_single(dumps(record))
    assert response.status == 200
    assert b", " not in response.body and b": " not in response.body
    tagged = make_record(annotations={"Ünknown": []})
    body = post_single(dumps(tagged)).body
    assert "/annotations/Ünknown".encode() in body


# ---------------------------------------------------------------- V08 preservation


def test_echo_preserves_nested_reserved_metadata_exactly() -> None:
    raw = example_bytes("nested-reserved-metadata.json")
    response = post_single(raw, query="echo=true")
    assert response.status == 200
    assert echo_bytes(response) == raw.strip()
    echoed = response.json()["echo"]
    assert echoed == json.loads(raw)
    assert echoed["echo"] is False  # a foreign "echo" property is data, not an option
    assert set(echoed["annotations"]) == {"SDrug", "AE"}  # omitted labels stay omitted
    names = [f["path"] for f in response.json()["foreign_fields"]]
    assert names == [
        "/__dict__",
        "/__proto__",
        "/_scatSource",
        "/echo",
        "/extras",
        "/id",
        "/label",
        "/model_config",
        "/nested",
        "/offsetUnit",
        "/annotations/SDrug/0/color",
        "/annotations/SDrug/0/extras",
        "/annotations/SDrug/0/id",
        "/annotations/SDrug/0/label",
        "/annotations/AE/0/__proto__",
        "/annotations/AE/0/_scatSource",
    ]
    types = {f["path"]: f["type"] for f in response.json()["foreign_fields"]}
    assert types["/echo"] == "boolean"
    assert types["/id"] == "number"
    assert types["/__dict__"] == "array"
    assert types["/__proto__"] == "object"


def test_foreign_echo_property_does_not_enable_echo() -> None:
    response = post_single(example_bytes("nested-reserved-metadata.json"))
    assert response.status == 200
    assert "echo" not in response.json()


def test_batch_echo_is_entire_envelope() -> None:
    raw = example_bytes("valid-batch.json")
    response = post_batch(raw, query="echo=true")
    assert response.status == 200
    assert echo_bytes(response) == raw.strip()


def test_validation_does_not_mutate_source_tree() -> None:
    from scat_validator.paths import PathNode
    from scat_validator.reporting import build_report
    from scat_validator.wire import decode_document

    raw = example_bytes("nested-reserved-metadata.json")
    value = decode_document(raw).value
    before = repr(value)
    build_report("single", [(value, PathNode.root())])
    assert repr(value) == before


# ---------------------------------------------------------------- V09 exact numbers


EXPECTED_NUMERIC_TOKENS = [
    b'"fraction": 0.1,',
    b'"precision": 0.100000000000000005,',
    b'"decimal_integer": 1.0,',
    b'"exponent_integer": 1e0,',
    b'"tiny": 1e-400,',
    b'"signed_zero": -0,',
    b'"scale": 1.2300,',
    b'"numeric_string": "9007199254740993"',
]


def test_numeric_tokens_survive_echo_byte_for_byte() -> None:
    raw = raw_fixture("numeric-metadata-record.json")
    response = post_single(raw, query="echo=true")
    assert response.status == 200
    echoed = echo_bytes(response)
    assert echoed == raw.strip()
    for token in EXPECTED_NUMERIC_TOKENS:
        assert token in echoed
    assert b'"/numeric_metadata","type":"object"' in response.body


def test_unsafe_numbers_fail_as_unsafe_number() -> None:
    response = post_single(raw_fixture("unsafe-numbers-record.json"), query="echo=true")
    assert response.status == 422
    assert issues_of(response) == [
        ("/unsafe/a", "UNSAFE_NUMBER"),
        ("/unsafe/b", "UNSAFE_NUMBER"),
        ("/unsafe/c", "UNSAFE_NUMBER"),
        ("/unsafe/d", "UNSAFE_NUMBER"),
        ("/unsafe/e", "UNSAFE_NUMBER"),
    ]
    assert "echo" not in response.json()


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_nonstandard_constants_are_invalid_json(token: str) -> None:
    raw = raw_fixture("nan-metadata.json").replace(b"NaN", token.encode())
    assert_request_failure(post_single(raw), 400, "INVALID_JSON", 1)


@pytest.mark.parametrize(
    "token",
    ["1e-99999999999999999999", "-1E-99999999999999999999", "0e99999999999999999999", "-0.0e+999"],
)
def test_extreme_exponents_with_safe_values_round_trip(token: str) -> None:
    raw = (
        b'{"project_id":1,"case_id":1,"username":"u","narrative":"n",'
        b'"offset_unit":"unicode-code-point","annotations":{},"x":' + token.encode() + b"}"
    )
    response = post_single(raw, query="echo=true")
    assert response.status == 200
    assert echo_bytes(response) == raw


def test_large_exponent_with_huge_coefficient_is_unsafe_without_500() -> None:
    raw = (
        b'{"project_id":1,"case_id":1,"username":"u","narrative":"n",'
        b'"offset_unit":"unicode-code-point","annotations":{},"x":-'
        + b"9" * 6000
        + b"."
        + b"9" * 6000
        + b"E+99999999999999999999}"
    )
    response = post_single(raw)
    assert response.status == 422
    assert issues_of(response) == [("/x", "UNSAFE_NUMBER")]


# ---------------------------------------------------------------- V11 decoder via HTTP


def test_one_leading_bom_accepted_and_removed_from_echo() -> None:
    raw = raw_fixture("bom-record.json")
    response = post_single(raw, query="echo=true")
    assert response.status == 200
    assert echo_bytes(response) == example_bytes("valid-record.json").strip()


def test_bom_counts_toward_body_limit() -> None:
    body = b"\xef\xbb\xbf" + example_bytes("valid-record.json")
    padding = MAX_BODY_BYTES - len(body) + 1
    oversized = body[:-2] + b" " * padding + body[-2:]
    assert len(oversized) == MAX_BODY_BYTES + 1
    assert_request_failure(post_single(oversized), 413, "PAYLOAD_TOO_LARGE", 0)
    within = oversized[:-3] + oversized[-2:]  # drop one padding space
    assert len(within) == MAX_BODY_BYTES
    assert post_single(within).status == 200


@pytest.mark.parametrize(
    "name",
    ["double-bom-record.json", "invalid-utf8.json", "nan-metadata.json"],
)
def test_decoder_failures_have_no_report(name: str) -> None:
    body = assert_request_failure(
        post_single(raw_fixture(name), query="echo=true"), 400, "INVALID_JSON", 1
    )
    assert body["issues"][0] == {
        "location": "body",
        "path": "",
        "code": "INVALID_JSON",
        "message": "The request body must be one valid UTF-8 JSON document.",
    }


@pytest.mark.parametrize(
    "body",
    [b"", b" \n", b'{"project_id": 1', b"{}{}", b"{} x", b"\xef\xbb\xbf", b'{"a": 1}\xef\xbb\xbf'],
)
def test_empty_malformed_trailing_and_misplaced_bom(body: bytes) -> None:
    assert_request_failure(post_single(body), 400, "INVALID_JSON", 1)


@pytest.mark.parametrize("name", ["duplicate-escaped-name.json", "duplicate-nested-name.json"])
def test_duplicate_names_fail_without_report(name: str) -> None:
    for post in (post_single, post_batch):
        body = assert_request_failure(
            post(raw_fixture(name), query="echo=true"), 400, "DUPLICATE_PROPERTY", 1
        )
        assert body["issues"][0]["path"] == ""


def test_feff_inside_strings_is_content() -> None:
    record = make_record(narrative="﻿Aspirin caused nausea.", annotations={}, extra="﻿")
    raw = json.dumps(record, ensure_ascii=False).encode()
    response = post_single(raw, query="echo=true")
    assert response.status == 200
    assert response.json()["records"][0]["narrative_code_points"] == 23
    assert echo_bytes(response) == raw


# ---------------------------------------------------------------- V12 transport


@pytest.mark.parametrize(
    "content_type",
    [
        "application/json",
        "Application/JSON",
        "application/json; charset=utf-8",
        "application/json;charset=UTF-8",
        'application/json; charset="utf-8"',
        'application/json ; CHARSET="UTF-8"',
        "application/json;",
    ],
)
def test_supported_media_types(content_type: str) -> None:
    headers = [(b"content-type", content_type.encode())]
    response = post_single(example_bytes("valid-record.json"), headers=headers)
    assert response.status == 200


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [(b"content-type", b"text/plain")],
        [(b"content-type", b"application/x-json")],
        [(b"content-type", b"application/json-patch+json")],
        [(b"content-type", b"application/*")],
        [(b"content-type", b"application/json; charset=latin-1")],
        [(b"content-type", b"application/json; charset=utf8")],
        [(b"content-type", b"application/json; charset=utf-16")],
        [(b"content-type", b"application/json; version=1")],
        [(b"content-type", b"application/json; charset=utf-8; charset=utf-8")],
        [(b"content-type", b"application/json; charset = utf-8")],
        [(b"content-type", b"application/json"), (b"content-type", b"application/json")],
        [(b"content-type", b"")],
    ],
)
def test_unsupported_media_types(headers: list[tuple[bytes, bytes]]) -> None:
    response = post_single(example_bytes("valid-record.json"), headers=headers)
    assert_request_failure(response, 415, "UNSUPPORTED_MEDIA_TYPE", 0)


# Empty values / empty list elements are ignored per RFC 9110 list rules.
@pytest.mark.parametrize(
    "encoding", [b"identity", b"IDENTITY", b" Identity ", b"", b" , ", b"identity,"]
)
def test_identity_content_encoding_accepted(encoding: bytes) -> None:
    headers = [*JSON_HEADERS, (b"content-encoding", encoding)]
    assert post_single(example_bytes("valid-record.json"), headers=headers).status == 200


@pytest.mark.parametrize("encoding", [b"gzip", b"br", b"deflate", b"identity, gzip", b"x-gzip"])
def test_compressed_bodies_rejected(encoding: bytes) -> None:
    headers = [*JSON_HEADERS, (b"content-encoding", encoding)]
    response = post_single(example_bytes("valid-record.json"), headers=headers)
    assert_request_failure(response, 415, "UNSUPPORTED_CONTENT_ENCODING", 0)


def padded_record(total: int) -> bytes:
    raw = example_bytes("valid-record.json").strip()
    padding = total - len(raw)
    assert padding >= 0
    return raw[:-1] + b" " * padding + raw[-1:]


def test_exact_byte_limit_passes_and_limit_plus_one_fails() -> None:
    exact = padded_record(MAX_BODY_BYTES)
    assert len(exact) == MAX_BODY_BYTES
    assert post_single(exact).status == 200
    assert_request_failure(
        post_single(padded_record(MAX_BODY_BYTES + 1)), 413, "PAYLOAD_TOO_LARGE", 0
    )


def test_streamed_body_without_content_length_is_bounded() -> None:
    chunk = b" " * 65536
    body_prefix = example_bytes("valid-record.json").strip()[:-1]
    chunks = [body_prefix] + [chunk] * 200 + [b"}"]  # ~13 MB offered
    response = post_single(b"", chunks=chunks)
    assert_request_failure(response, 413, "PAYLOAD_TOO_LARGE", 0)
    # Reading stopped right after the limit was crossed, not after the whole stream.
    assert response.receive_calls <= MAX_BODY_BYTES // len(chunk) + 2
    assert response.receive_calls < response.chunks_offered


def test_streamed_body_within_limit_is_accepted() -> None:
    raw = example_bytes("valid-record.json")
    chunks = [raw[i : i + 7] for i in range(0, len(raw), 7)]
    response = post_single(b"", chunks=chunks)
    assert response.status == 200


def test_declared_oversize_rejected_before_reading_body() -> None:
    headers = [*JSON_HEADERS, (b"content-length", str(MAX_BODY_BYTES + 1).encode())]
    response = post_single(b"{}", headers=headers, content_length=False)
    assert_request_failure(response, 413, "PAYLOAD_TOO_LARGE", 0)
    assert response.receive_calls == 0
    huge = [*JSON_HEADERS, (b"content-length", b"9" * 5000)]
    assert post_single(b"{}", headers=huge, content_length=False).status == 413


def test_all_post_responses_are_no_store_json() -> None:
    cases = [
        post_single(example_bytes("valid-record.json")),
        post_batch(example_bytes("invalid-batch.json")),
        post_single(b"{", query="x=1"),
        post_single(b"{}", headers=[]),
    ]
    for response in cases:
        assert response.header("cache-control") == "no-store"
        assert response.header("content-type") == "application/json"


# ---------------------------------------------------------------- V13 depth and batch bounds


def deep_record(depth: int) -> bytes:
    """A valid record whose deepest container is at ``depth`` (root object is depth 1)."""
    raw = example_bytes("valid-record.json").strip()
    nested = "[" * (depth - 1) + "]" * (depth - 1)
    return raw[:-1] + f', "deep": {nested}}}'.encode()


def test_depth_128_passes_and_129_fails() -> None:
    assert post_single(deep_record(128)).status == 200
    body = assert_request_failure(post_single(deep_record(129)), 422, "INVALID_DOCUMENT", 1)
    assert body["issues"][0]["code"] == "LIMIT_EXCEEDED"
    assert body["issues"][0]["path"] == ""


def test_batch_depth_counts_envelope_and_records_array() -> None:
    record = deep_record(126).decode()
    assert post_batch(f'{{"records": [{record}]}}').status == 200
    record = deep_record(127).decode()
    response = post_batch(f'{{"records": [{record}]}}')
    assert_request_failure(response, 422, "INVALID_DOCUMENT", 1)


def batch_of(count: int) -> bytes:
    records = [make_record(case_id=i + 1) for i in range(count)]
    return dumps({"records": records})


def test_batch_sizes() -> None:
    assert post_batch(batch_of(1)).status == 200
    response = post_batch(batch_of(100))
    assert response.status == 200
    assert response.json()["record_count"] == 100
    for count in (0, 101):
        body = assert_request_failure(post_batch(batch_of(count)), 422, "INVALID_DOCUMENT", 1)
        assert body["issues"][0]["path"] == "/records"
        assert body["issues"][0]["code"] == "LIMIT_EXCEEDED"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (b"[]", [("", "INVALID_TYPE")]),
        (b'"x"', [("", "INVALID_TYPE")]),
        (b"{}", [("/records", "REQUIRED_FIELD")]),
        (b'{"records": null}', [("/records", "INVALID_TYPE")]),
        (b'{"records": {}}', [("/records", "INVALID_TYPE")]),
        (
            b'{"records": [], "zeta": 1}',
            [("/records", "LIMIT_EXCEEDED"), ("/zeta", "UNKNOWN_FIELD")],
        ),
        (
            b'{"alpha": 1, "Records": [], "records": [{}], "echo": true}',
            [
                ("/Records", "UNKNOWN_FIELD"),
                ("/alpha", "UNKNOWN_FIELD"),
                ("/echo", "UNKNOWN_FIELD"),
            ],
        ),
        (b'{"a/b~c": 1}', [("/a~1b~0c", "UNKNOWN_FIELD"), ("/records", "REQUIRED_FIELD")]),
        (b'{"\\u0000": 1, "records": [{}]}', [("", "UNSUPPORTED_UNICODE")]),
        (b'{"": 1, "records": [{}]}', [("/", "UNKNOWN_FIELD")]),
    ],
)
def test_envelope_failures(body: bytes, expected: list[tuple[str, str]]) -> None:
    response = post_batch(body, query="echo=true")
    assert_request_failure(response, 422, "INVALID_DOCUMENT", len(expected))
    assert issues_of(response) == expected


def test_non_object_entries_are_evaluated_slots() -> None:
    records = [
        make_record(),
        5,
        None,
        "x",
        [],
        make_record(case_id=2, narrative="", annotations={}),
    ]
    response = post_batch(dumps({"records": records}))
    body = response.json()
    assert response.status == 422
    assert body["record_count"] == 6
    assert (body["valid_record_count"], body["invalid_record_count"]) == (1, 5)
    assert [r["valid"] for r in body["records"]] == [True, False, False, False, False, False]
    assert issues_of(response)[-1] == ("/records/5/narrative", "EMPTY_VALUE")


# ---------------------------------------------------------------- V14 query and echo

TRUE_SPELLINGS = ["true", "1", "on", "yes", "t", "y"]
FALSE_SPELLINGS = ["false", "0", "off", "no", "f", "n"]


def case_variants(word: str) -> set[str]:
    return {word, word.upper(), word.capitalize(), word.swapcase()} | (
        {word[0].upper() + word[1:].lower()} if word else set()
    )


@pytest.mark.parametrize("spelling", sorted({v for w in TRUE_SPELLINGS for v in case_variants(w)}))
def test_true_spellings_echo_on_success(spelling: str) -> None:
    raw = example_bytes("valid-record.json")
    response = post_single(raw, query=f"echo={spelling}")
    assert response.status == 200
    assert echo_bytes(response) == raw.strip()
    batch = post_batch(example_bytes("valid-batch.json"), query=f"echo={spelling}")
    assert "echo" in batch.json()


@pytest.mark.parametrize("spelling", sorted({v for w in FALSE_SPELLINGS for v in case_variants(w)}))
def test_false_spellings_omit_echo(spelling: str) -> None:
    response = post_single(example_bytes("valid-record.json"), query=f"echo={spelling}")
    assert response.status == 200
    assert "echo" not in response.json()


def test_default_omits_echo() -> None:
    assert "echo" not in post_single(example_bytes("valid-record.json")).json()


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("echo=maybe", [("/echo", "INVALID_QUERY_PARAMETER")]),
        ("echo=", [("/echo", "INVALID_QUERY_PARAMETER")]),
        ("echo", [("/echo", "INVALID_QUERY_PARAMETER")]),
        ("echo=%20true", [("/echo", "INVALID_QUERY_PARAMETER")]),
        ("echo=true%20", [("/echo", "INVALID_QUERY_PARAMETER")]),
        ("echo=+true", [("/echo", "INVALID_QUERY_PARAMETER")]),
        ("echo=tru", [("/echo", "INVALID_QUERY_PARAMETER")]),
        ("echo=2", [("/echo", "INVALID_QUERY_PARAMETER")]),
        ("echo=%00", [("/echo", "INVALID_QUERY_PARAMETER")]),
        ("echo=%FF", [("/echo", "INVALID_QUERY_PARAMETER")]),
        ("echo=true&echo=true", [("/echo", "DUPLICATE_QUERY_PARAMETER")]),
        ("echo=true&echo=maybe", [("/echo", "DUPLICATE_QUERY_PARAMETER")]),
        ("Echo=true", [("/Echo", "UNKNOWN_QUERY_PARAMETER")]),
        (
            "z=1&a=2&z=3&echo=x",
            [
                ("/a", "UNKNOWN_QUERY_PARAMETER"),
                ("/echo", "INVALID_QUERY_PARAMETER"),
                ("/z", "UNKNOWN_QUERY_PARAMETER"),
            ],
        ),
        ("a%2Fb=1", [("/a~1b", "UNKNOWN_QUERY_PARAMETER")]),
        ("%FF=1", [("", "UNSUPPORTED_UNICODE")]),
        ("=1", [("/", "UNKNOWN_QUERY_PARAMETER")]),
    ],
)
def test_invalid_query_options(query: str, expected: list[tuple[str, str]]) -> None:
    for post in (post_single, post_batch):
        fixture = "valid-record.json" if post is post_single else "valid-batch.json"
        response = post(example_bytes(fixture), query=query)
        body = assert_request_failure(response, 422, "INVALID_OPTIONS", len(expected))
        assert [(i["path"], i["code"]) for i in body["issues"]] == expected
        assert all(i["location"] == "query" for i in body["issues"])


def test_invalid_echo_example_matches_spec() -> None:
    body = post_single(example_bytes("valid-record.json"), query="echo=maybe").json()
    assert body == {
        "valid": False,
        "mode": "single",
        "error": {"code": "INVALID_OPTIONS", "message": "The query options are invalid."},
        "issues": [
            {
                "location": "query",
                "path": "/echo",
                "code": "INVALID_QUERY_PARAMETER",
                "message": "echo must use a supported boolean spelling.",
            }
        ],
        "issue_count": 1,
        "omitted_issue_count": 0,
        "checks_not_performed": [
            "authentication",
            "authorization",
            "database_references",
            "previous_submission_detection",
            "clinical_correctness",
        ],
    }


def test_empty_query_segments_are_ignored() -> None:
    assert post_single(example_bytes("valid-record.json"), query="&&echo=1&").status == 200


# ---------------------------------------------------------------- V21 gate precedence


def test_wrong_media_type_beats_bad_json() -> None:
    response = post_single(b"{bad", headers=[(b"content-type", b"text/plain")])
    assert_request_failure(response, 415, "UNSUPPORTED_MEDIA_TYPE", 0)


def test_media_type_beats_content_encoding() -> None:
    headers = [(b"content-type", b"text/plain"), (b"content-encoding", b"gzip")]
    assert_request_failure(post_single(b"{}", headers=headers), 415, "UNSUPPORTED_MEDIA_TYPE", 0)


def test_encoding_beats_size() -> None:
    headers = [*JSON_HEADERS, (b"content-encoding", b"gzip")]
    response = post_single(padded_record(MAX_BODY_BYTES + 1), headers=headers)
    assert_request_failure(response, 415, "UNSUPPORTED_CONTENT_ENCODING", 0)


def test_oversize_beats_invalid_echo() -> None:
    response = post_single(padded_record(MAX_BODY_BYTES + 1), query="echo=maybe")
    assert_request_failure(response, 413, "PAYLOAD_TOO_LARGE", 0)


def test_bad_json_beats_invalid_echo() -> None:
    assert_request_failure(post_single(b"{bad", query="echo=maybe"), 400, "INVALID_JSON", 1)


def test_depth_beats_invalid_echo() -> None:
    response = post_single(deep_record(129), query="echo=maybe")
    assert_request_failure(response, 422, "INVALID_DOCUMENT", 1)


def test_invalid_echo_beats_bad_envelope_and_bad_records() -> None:
    assert_request_failure(
        post_batch(b'{"nope": 1}', query="echo=maybe"), 422, "INVALID_OPTIONS", 1
    )
    assert_request_failure(post_single(b"[]", query="unknown=1"), 422, "INVALID_OPTIONS", 1)
    assert_request_failure(
        post_batch(example_bytes("invalid-batch.json"), query="echo=maybe"),
        422,
        "INVALID_OPTIONS",
        1,
    )


# ---------------------------------------------------------------- V22 repeatability


def test_repeated_requests_are_equivalent_and_allocate_nothing() -> None:
    raw = example_bytes("valid-record.json")
    first = post_single(raw, query="echo=true")
    second = post_single(raw, query="echo=true")
    assert first.body == second.body
    batch = example_bytes("repeated-case-pair-batch.json")
    assert post_batch(batch).body == post_batch(batch).body
    # The same record in separate requests is never a duplicate.
    assert post_single(raw).status == 200
    assert not re.search(rb'"(id|import_id|stored)"', first.body.split(b',"echo":')[0])


# ---------------------------------------------------------------- framework responses


def test_unrelated_methods_use_framework_defaults() -> None:
    assert call("GET", "/api/v1/validate").status == 405
    assert call("GET", "/nope").status == 404
