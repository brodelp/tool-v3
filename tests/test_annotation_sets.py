"""annotation_sets record contract through the real HTTP path (stage 4 spec §2)."""

from typing import Any

import pytest

from scat_validator.config import MAX_BODY_BYTES, MAX_REPORT_BYTES
from tests.helpers import (
    MISSING,
    RawResponse,
    checks_of,
    dumps,
    issue_triples,
    make_record,
    post_single,
    user_set,
    with_user_annotations,
)

LLM_ID = "llm:anthropic:claude-x"
GENERATED_AT = "2026-10-29T14:05:09Z"
ASPIRIN = {"text": "Aspirin", "start": 0, "end": 7}
NAUSEA = {"text": "nausea", "start": 15, "end": 21}


def llm_set(
    annotations: Any,
    provider: str = "anthropic",
    model: str = "claude-x",
    generated_at: str = GENERATED_AT,
) -> dict[str, Any]:
    return {
        "kind": "llm",
        "annotations": annotations,
        "provider": provider,
        "model": model,
        "generated_at": generated_at,
    }


def with_sets(sets: Any) -> dict[str, Any]:
    return make_record(annotation_sets=sets, source_metadata=MISSING)


def single(record: Any) -> RawResponse:
    return post_single(dumps(record))


def observation(response: RawResponse) -> Any:
    return response.json()["records"][0]


def test_user_and_llm_sets_pass() -> None:
    response = single(
        with_sets(
            {
                "user": user_set({"SDrug": [ASPIRIN], "AE": [NAUSEA]}),
                LLM_ID: llm_set({"AE": [NAUSEA]}),
            }
        )
    )
    assert response.status == 200, response.json()
    assert issue_triples(response) == []
    record = observation(response)
    assert record["annotation_count"] == 3
    assert record["annotation_sets"] == {
        LLM_ID: {"kind": "llm", "annotation_count": 1, "annotations_by_label": {"AE": 1}},
        "user": {
            "kind": "user",
            "annotation_count": 2,
            "annotations_by_label": {"SDrug": 1, "AE": 1},
        },
    }
    assert list(record["annotation_sets"]) == [LLM_ID, "user"]
    assert "annotations_by_label" not in record
    assert response.json()["foreign_fields"] == []


@pytest.mark.parametrize(
    ("legacy", "expected"),
    [
        (b'{"AE":[{"text":"nausea","start":15,"end":21}]}', []),
        # Its content is not validated as a map, but every value still passes json_values.
        (b'{"AE":[{"start":1e400}]}', [("body", "/annotations/AE/0/start", "UNSAFE_NUMBER")]),
        (b'"not a map"', []),
    ],
)
def test_legacy_annotations_member_is_superseded(
    legacy: bytes, expected: list[tuple[str, str, str]]
) -> None:
    raw = (
        b'{"case_id":"5001-1","username":"demo_annotator","narrative":"Aspirin caused nausea.",'
        b'"annotations":' + legacy + b"}"
    )
    response = post_single(raw)
    assert response.status == 422
    assert issue_triples(response) == [*expected, ("body", "/annotations", "SUPERSEDED_FIELD")]
    body = response.json()
    assert body["issues"][-1]["message"] == "Use annotation_sets.user.annotations instead."
    assert body["foreign_fields"] == []
    assert body["foreign_field_count"] == 0
    assert checks_of(response)["field_values"] == "failed"
    record_observation = observation(response)
    assert record_observation["annotation_count"] == 0
    assert record_observation["annotation_sets"] == {}


@pytest.mark.parametrize("set_id", ["User", "llm:", "llm:Anthropic:x", "llm:anthropic:", "human"])
def test_invalid_set_ids(set_id: str) -> None:
    bad_range = {"AE": [{"text": "nausea", "start": 15, "end": 99}]}
    value = llm_set(bad_range) if set_id.startswith("llm:") else user_set(bad_range)
    response = single(with_sets({set_id: value}))
    assert issue_triples(response) == [
        ("body", f"/annotation_sets/{set_id}", "INVALID_SET_ID"),
        ("body", f"/annotation_sets/{set_id}/annotations/AE/0", "INVALID_RANGE"),
    ]
    assert response.json()["issues"][0]["message"] == (
        "A set id must be user or llm:<provider>:<model>."
    )
    # An invalid id makes the set map unusable for counting, as an unknown label does.
    record = observation(response)
    assert record["annotation_count"] is None
    assert record["annotation_sets"] is None


def test_model_may_contain_colons() -> None:
    sets = {"llm:openai:llama3.1:8b": llm_set({}, provider="openai", model="llama3.1:8b")}
    response = single(with_sets(sets))
    assert response.status == 200, response.json()


USER_KIND = "/annotation_sets/user/kind"
LLM_KIND = f"/annotation_sets/{LLM_ID}/kind"


@pytest.mark.parametrize(
    ("set_id", "value", "expected"),
    [
        ("user", {**llm_set({}), "kind": "llm"}, [(USER_KIND, "INVALID_SET_KIND")]),
        (LLM_ID, {**llm_set({}), "kind": "user"}, [(LLM_KIND, "INVALID_SET_KIND")]),
        ("user", {"kind": "bot", "annotations": {}}, [(USER_KIND, "INVALID_SET_KIND")]),
        (LLM_ID, {**llm_set({}), "kind": "LLM"}, [(LLM_KIND, "INVALID_SET_KIND")]),
        ("user", {"kind": 1, "annotations": {}}, [(USER_KIND, "INVALID_TYPE")]),
    ],
)
def test_kind_must_agree_with_id(
    set_id: str, value: dict[str, Any], expected: list[tuple[str, str]]
) -> None:
    response = single(with_sets({set_id: value}))
    assert issue_triples(response) == [("body", path, code) for path, code in expected]
    message = response.json()["issues"][0]["message"]
    if expected[0][1] == "INVALID_SET_KIND":
        assert message == "kind must be user for the user set and llm for an llm set."
    else:
        assert message == "Must be a JSON string."
    observed_kind = value["kind"] if value["kind"] in ("user", "llm") else None
    assert observation(response)["annotation_sets"][set_id]["kind"] == observed_kind


def test_user_set_provenance_is_foreign_metadata() -> None:
    value = {**llm_set({}), "kind": "user", "provider": 1}
    response = single(with_sets({"user": value}))
    assert response.status == 200, response.json()
    assert response.json()["foreign_fields"] == [
        {"path": "/annotation_sets/user/generated_at", "type": "string"},
        {"path": "/annotation_sets/user/model", "type": "string"},
        {"path": "/annotation_sets/user/provider", "type": "number"},
    ]


@pytest.mark.parametrize(
    ("value", "missing"),
    [
        ({"kind": "llm", "annotations": {}}, ["provider", "model", "generated_at"]),
        ({}, ["kind", "annotations", "provider", "model", "generated_at"]),
    ],
)
def test_llm_set_requires_provenance(value: dict[str, Any], missing: list[str]) -> None:
    response = single(with_sets({LLM_ID: value}))
    assert issue_triples(response) == [
        ("body", f"/annotation_sets/{LLM_ID}/{name}", "REQUIRED_FIELD") for name in missing
    ]
    assert checks_of(response)["required_fields"] == "failed"


def test_user_set_requires_kind_and_annotations() -> None:
    response = single(with_sets({"user": {}}))
    assert issue_triples(response) == [
        ("body", "/annotation_sets/user/kind", "REQUIRED_FIELD"),
        ("body", "/annotation_sets/user/annotations", "REQUIRED_FIELD"),
    ]
    checks = checks_of(response)
    assert checks["label_membership"] == "skipped"
    assert observation(response)["annotation_sets"] == {
        "user": {"kind": None, "annotation_count": None, "annotations_by_label": None}
    }


@pytest.mark.parametrize(
    ("provider", "model", "path"),
    [
        ("anthropic", "claude-y", "model"),
        ("openai", "claude-x", "provider"),
    ],
)
def test_provenance_must_match_id(provider: str, model: str, path: str) -> None:
    response = single(with_sets({LLM_ID: llm_set({}, provider=provider, model=model)}))
    assert issue_triples(response) == [
        ("body", f"/annotation_sets/{LLM_ID}/{path}", "SET_ID_MISMATCH")
    ]
    assert response.json()["issues"][0]["message"] == "provider and model must match the set id."


def test_provenance_types() -> None:
    value = {**llm_set({}), "provider": None, "model": 5, "generated_at": False}
    response = single(with_sets({LLM_ID: value}))
    assert issue_triples(response) == [
        ("body", f"/annotation_sets/{LLM_ID}/{name}", "INVALID_TYPE")
        for name in ("provider", "model", "generated_at")
    ]


@pytest.mark.parametrize(
    ("generated_at", "valid"),
    [
        ("2026-10-29T14:05:09Z", True),
        ("2026-10-29T14:05:09.123456Z", True),
        ("2026-10-29T14:05:09+00:00", False),
        ("2026-02-30T00:00:00Z", False),
        ("2026-10-29T14:05:09.1234567Z", False),
        ("2026-10-29T24:00:00Z", False),
        ("2026-10-29t14:05:09z", False),
        ("2026-10-29T14:05:09Z\n", False),
        ("\uff12026-10-29T14:05:09Z", False),
    ],
)
def test_generated_at_format(generated_at: str, valid: bool) -> None:
    response = single(with_sets({LLM_ID: llm_set({}, generated_at=generated_at)}))
    if valid:
        assert issue_triples(response) == []
    else:
        assert issue_triples(response) == [
            ("body", f"/annotation_sets/{LLM_ID}/generated_at", "INVALID_TIMESTAMP")
        ]
        assert response.json()["issues"][0]["message"] == (
            "generated_at must be an RFC 3339 UTC timestamp ending in Z."
        )


@pytest.mark.parametrize(("count", "valid"), [(16, True), (17, False)])
def test_set_limit(count: int, valid: bool) -> None:
    sets = {f"llm:p:m{i:02}": llm_set({}, provider="p", model=f"m{i:02}") for i in range(count)}
    response = single(with_sets(sets))
    if valid:
        assert issue_triples(response) == []
        assert len(observation(response)["annotation_sets"]) == 16
    else:
        assert issue_triples(response) == [("body", "/annotation_sets", "LIMIT_EXCEEDED")]
        assert response.json()["issues"][0]["message"] == (
            "annotation_sets must not have more than 16 sets."
        )
        # Over the limit the per-set observation is withheld so reports stay bounded.
        assert observation(response)["annotation_sets"] is None
        assert observation(response)["annotation_count"] is None


def test_many_sets_and_long_ids_stay_bounded() -> None:
    sets = {f"llm:p:m{i:06}": llm_set({}, provider="p", model=f"m{i:06}") for i in range(30_000)}
    sets["llm:p:" + "m" * 1_048_576] = llm_set({})
    raw = dumps(with_sets(sets))
    assert len(raw) < MAX_BODY_BYTES
    response = post_single(raw)
    assert response.status == 422
    assert len(response.body) <= MAX_REPORT_BYTES
    assert response.json()["issue_count"] == 2  # LIMIT_EXCEEDED, then the overlong id
    assert issue_triples(response)[0] == ("body", "/annotation_sets", "LIMIT_EXCEEDED")


def test_duplicates_are_per_set() -> None:
    passing = single(
        with_sets({"user": user_set({"AE": [NAUSEA]}), LLM_ID: llm_set({"AE": [NAUSEA]})})
    )
    assert issue_triples(passing) == []
    failing = single(with_sets({"user": user_set({"AE": [NAUSEA, NAUSEA]})}))
    assert issue_triples(failing) == [
        ("body", "/annotation_sets/user/annotations/AE/1", "DUPLICATE_ANNOTATION")
    ]


def test_model_with_slash_is_escaped_in_paths() -> None:
    set_id = "llm:openai:org/m:8b"
    bad_quote = {"AE": [{"text": "Nausea", "start": 15, "end": 21}]}
    response = single(with_sets({set_id: llm_set(bad_quote, provider="openai", model="org/m:8b")}))
    assert issue_triples(response) == [
        ("body", "/annotation_sets/llm:openai:org~1m:8b/annotations/AE/0/text", "TEXT_MISMATCH")
    ]


def test_issue_order_is_sets_in_id_order_then_set_fields_then_labels() -> None:
    sets = {
        "user": {"kind": "llm", "annotations": {"zz": [], "AE": [{"text": "x"}]}},
        LLM_ID: {"kind": "llm", "annotations": {"AE": [{}]}, "provider": "anthropic"},
    }
    response = single(with_sets(sets))
    llm = f"/annotation_sets/{LLM_ID}"
    assert issue_triples(response) == [
        ("body", f"{llm}/model", "REQUIRED_FIELD"),
        ("body", f"{llm}/generated_at", "REQUIRED_FIELD"),
        ("body", f"{llm}/annotations/AE/0/text", "REQUIRED_FIELD"),
        ("body", f"{llm}/annotations/AE/0/start", "REQUIRED_FIELD"),
        ("body", f"{llm}/annotations/AE/0/end", "REQUIRED_FIELD"),
        ("body", "/annotation_sets/user/provider", "REQUIRED_FIELD"),
        ("body", "/annotation_sets/user/model", "REQUIRED_FIELD"),
        ("body", "/annotation_sets/user/generated_at", "REQUIRED_FIELD"),
        ("body", "/annotation_sets/user/annotations/AE/0/start", "REQUIRED_FIELD"),
        ("body", "/annotation_sets/user/annotations/AE/0/end", "REQUIRED_FIELD"),
        ("body", "/annotation_sets/user/kind", "INVALID_SET_KIND"),
        ("body", "/annotation_sets/user/annotations/zz", "UNKNOWN_LABEL"),
    ]


def test_set_foreign_metadata_is_inventoried_and_echoed() -> None:
    raw = (
        b'{"case_id":"5001-1","username":"demo_annotator","narrative":"Aspirin caused nausea.",'
        b'"annotation_sets":{"user":{"note":1.0E2,"kind":"user","annotations":{"AE":'
        b'[{"text":"nausea","start":15,"end":21,"ref":-0.0}]}}},"z":1}'
    )
    response = post_single(raw, query="echo=true")
    assert response.status == 200, response.json()
    body = response.json()
    assert body["foreign_fields"] == [
        {"path": "/z", "type": "number"},
        {"path": "/annotation_sets/user/note", "type": "number"},
        {"path": "/annotation_sets/user/annotations/AE/0/ref", "type": "number"},
    ]
    assert response.body.endswith(b',"echo":' + raw + b"}")


@pytest.mark.parametrize("sets", [MISSING, {}])
def test_sets_absent_is_zero(sets: Any) -> None:
    response = single(with_sets(sets))
    assert response.status == 200, response.json()
    record = observation(response)
    assert record["annotation_count"] == 0
    assert record["annotation_sets"] == {}


@pytest.mark.parametrize("sets", [None, [], "user", 1])
def test_sets_not_object_is_null_counts(sets: Any) -> None:
    response = single(with_sets(sets))
    assert issue_triples(response) == [("body", "/annotation_sets", "INVALID_TYPE")]
    assert response.json()["issues"][0]["message"] == (
        "annotation_sets must be a JSON object mapping set ids to set objects."
    )
    record = observation(response)
    assert record["annotation_count"] is None
    assert record["annotation_sets"] is None
    checks = checks_of(response)
    assert checks["field_types"] == "failed"
    for name in ("label_membership", "offset_ranges", "text_matches", "annotation_duplicates"):
        assert checks[name] == "skipped"


def test_unusable_set_map_nulls_total() -> None:
    sets = {
        "user": user_set([]),
        LLM_ID: llm_set({"AE": [NAUSEA], "SDrug": [ASPIRIN]}),
        "llm:openai:gpt": 5,
    }
    response = single(with_sets(sets))
    assert issue_triples(response) == [
        ("body", "/annotation_sets/llm:openai:gpt", "INVALID_TYPE"),
        ("body", "/annotation_sets/user/annotations", "INVALID_TYPE"),
    ]
    messages = [issue["message"] for issue in response.json()["issues"]]
    assert messages == [
        "An annotation set must be a JSON object.",
        "annotations must be a JSON object mapping label names to arrays.",
    ]
    record = observation(response)
    assert record["annotation_count"] is None
    assert record["annotation_sets"] == {
        LLM_ID: {
            "kind": "llm",
            "annotation_count": 2,
            "annotations_by_label": {"SDrug": 1, "AE": 1},
        },
        "llm:openai:gpt": {"kind": None, "annotation_count": None, "annotations_by_label": None},
        "user": {"kind": "user", "annotation_count": None, "annotations_by_label": None},
    }


def test_superseded_and_sets_together() -> None:
    record = with_user_annotations({"AE": [NAUSEA]}, source_metadata=MISSING)
    raw = dumps(record)[:-1] + b',"annotations":{}}'
    response = post_single(raw)
    assert issue_triples(response) == [("body", "/annotations", "SUPERSEDED_FIELD")]
    assert observation(response)["annotation_count"] == 1
