"""OpenAPI contract and schema/runtime agreement (V20)."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from scat_validator.config import LABELS, MAX_SAFE_NUMBER
from scat_validator.contracts import (
    CASE_ID_PATTERN,
    REQUEST_FAILURE_ADAPTER,
    VALIDATION_REPORT_ADAPTER,
    BatchEnvelope,
    Record,
)
from scat_validator.openapi_docs import EXAMPLE_INVALID_BATCH, EXAMPLE_RECORD
from tests.helpers import call, dumps, example_bytes, post_batch, post_single

POST_PATHS = ("/api/v1/validate", "/api/v1/validate-batch")


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    response = call("GET", "/openapi.json")
    assert response.status == 200
    document: dict[str, Any] = response.json()
    return document


def schemas(spec: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = spec["components"]["schemas"]
    return result


def iter_refs(node: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(node, dict):
        if isinstance(node.get("$ref"), str):
            refs.append(node["$ref"])
        for value in node.values():
            refs.extend(iter_refs(value))
    elif isinstance(node, list):
        for value in node:
            refs.extend(iter_refs(value))
    return refs


def test_all_references_resolve(spec: dict[str, Any]) -> None:
    refs = iter_refs(spec)
    assert refs
    for ref in refs:
        assert ref.startswith("#/components/schemas/"), ref
        assert ref.removeprefix("#/components/schemas/") in schemas(spec), ref


def test_post_operations_document_body_parameters_and_all_statuses(spec: dict[str, Any]) -> None:
    for path, body_schema in zip(POST_PATHS, ("Record", "BatchEnvelope"), strict=True):
        operation = spec["paths"][path]["post"]
        body = operation["requestBody"]
        assert body["required"] is True
        assert body["content"]["application/json"]["schema"] == {
            "$ref": f"#/components/schemas/{body_schema}"
        }
        assert set(operation["responses"]) == {"200", "400", "413", "415", "422", "500"}
        for response in operation["responses"].values():
            assert response["headers"]["Cache-Control"]["schema"]["enum"] == ["no-store"]
        unprocessable = operation["responses"]["422"]["content"]["application/json"]["schema"]
        assert unprocessable == {
            "oneOf": [
                {"$ref": "#/components/schemas/ValidationReport"},
                {"$ref": "#/components/schemas/RequestFailure"},
            ]
        }
        ok = operation["responses"]["200"]["content"]["application/json"]["schema"]
        assert ok == {"$ref": "#/components/schemas/ValidationReport"}
        for status in ("400", "413", "415", "500"):
            schema = operation["responses"][status]["content"]["application/json"]["schema"]
            assert schema == {"$ref": "#/components/schemas/RequestFailure"}
        description = operation["description"]
        for fragment in ("5242880", "128", "9007199254740991", "1024", "256", "1048576", "8388608"):
            assert fragment in description


def test_echo_parameter(spec: dict[str, Any]) -> None:
    for path in POST_PATHS:
        (parameter,) = spec["paths"][path]["post"]["parameters"]
        assert parameter["name"] == "echo"
        assert parameter["in"] == "query"
        assert parameter["required"] is False
        assert parameter["schema"] == {"type": "boolean", "default": False}
        for spelling in (
            "`true`",
            "`1`",
            "`on`",
            "`yes`",
            "`t`",
            "`y`",
            "`false`",
            "`0`",
            "`off`",
            "`no`",
            "`f`",
            "`n`",
            "repeated",
            "other query parameter",
        ):
            assert spelling in parameter["description"]


def test_record_schema(spec: dict[str, Any]) -> None:
    record = schemas(spec)["Record"]
    assert record["additionalProperties"] is True
    assert record["required"] == ["case_id", "username", "narrative"]
    assert record["properties"]["offset_unit"]["default"] == "unicode-code-point"
    assert "default" not in record["properties"]["annotations"]
    assert "project_id" not in record["properties"]
    case_id = record["properties"]["case_id"]
    assert (case_id["type"], case_id["minLength"], case_id["maxLength"]) == ("string", 1, 64)
    assert case_id["pattern"] == CASE_ID_PATTERN
    assert case_id["examples"] == ["12345678-1"]
    username = record["properties"]["username"]
    assert username["maxLength"] == 255
    assert "format" not in username
    assert [branch.get("format") for branch in username["anyOf"]] == [None, "email"]
    assert record["properties"]["offset_unit"]["const"] == "unicode-code-point"


def test_annotation_map_and_annotation_schemas(spec: dict[str, Any]) -> None:
    annotation_map = schemas(spec)["AnnotationMap"]
    assert annotation_map["additionalProperties"] is False
    assert list(annotation_map["properties"]) == list(LABELS)
    assert "required" not in annotation_map
    for prop in annotation_map["properties"].values():
        assert prop["type"] == "array"
        assert prop["items"] == {"$ref": "#/components/schemas/Annotation"}
    annotation = schemas(spec)["Annotation"]
    assert annotation["additionalProperties"] is True
    assert annotation["required"] == ["text", "start", "end"]
    for name in ("start", "end"):
        prop = annotation["properties"][name]
        assert (prop["type"], prop["minimum"], prop["maximum"]) == ("integer", 0, MAX_SAFE_NUMBER)
        assert "format" not in prop


def test_batch_envelope_is_closed(spec: dict[str, Any]) -> None:
    envelope = schemas(spec)["BatchEnvelope"]
    assert envelope["additionalProperties"] is False
    assert envelope["required"] == ["records"]
    records = envelope["properties"]["records"]
    assert (records["minItems"], records["maxItems"]) == (1, 100)
    assert records["items"] == {"$ref": "#/components/schemas/Record"}


def test_response_variants(spec: dict[str, Any]) -> None:
    report = schemas(spec)["ValidationReport"]
    failure = schemas(spec)["RequestFailure"]
    assert "echo" in report["properties"] and "echo" not in report["required"]
    assert "error" in report["properties"] and "error" not in report["required"]
    for name in ("records", "record_count", "foreign_fields", "echo"):
        assert name not in failure["properties"]
    assert "error" in failure["required"]
    assert failure["properties"]["valid"]["const"] is False
    assert report["properties"]["issues"]["maxItems"] == 20
    assert report["properties"]["foreign_fields"]["maxItems"] == 100
    checks = schemas(spec)["Checks"]
    assert list(checks["properties"]) == [
        "json_values",
        "required_fields",
        "field_types",
        "field_values",
        "label_membership",
        "offset_ranges",
        "text_matches",
        "annotation_duplicates",
        "record_identity_uniqueness",
    ]
    status_enum = schemas(spec)["CheckStatus"]["enum"]
    assert status_enum == ["passed", "failed", "skipped"]
    observation = schemas(spec)["RecordObservation"]
    assert len(observation["required"]) == 11
    assert "project_id" not in observation["properties"]
    for nullable in ("case_id", "username", "offset_unit", "annotation_count"):
        types = [branch.get("type") for branch in observation["properties"][nullable]["anyOf"]]
        assert "null" in types
    issue = schemas(spec)["Issue"]
    assert "path_truncated" not in issue["required"]
    assert issue["properties"]["path_truncated"]["const"] is True
    error_codes = schemas(spec)["ErrorCode"]["enum"]
    assert set(error_codes) == {
        "INVALID_JSON",
        "DUPLICATE_PROPERTY",
        "PAYLOAD_TOO_LARGE",
        "UNSUPPORTED_MEDIA_TYPE",
        "UNSUPPORTED_CONTENT_ENCODING",
        "INVALID_OPTIONS",
        "INVALID_DOCUMENT",
        "INTERNAL_ERROR",
    }


def examples_for(operation: dict[str, Any], status: str) -> dict[str, Any]:
    content = operation["responses"][status]["content"]["application/json"]
    result: dict[str, Any] = content.get("examples", {})
    return result


def test_examples_match_runtime(spec: dict[str, Any]) -> None:
    single = spec["paths"]["/api/v1/validate"]["post"]
    request = single["requestBody"]["content"]["application/json"]["examples"]["valid_record"]
    response = post_single(dumps(request["value"]))
    assert response.json() == examples_for(single, "200")["valid_record"]["value"]
    invalid = post_single(dumps(request["value"]), query="echo=maybe")
    assert invalid.json() == examples_for(single, "422")["invalid_echo_option"]["value"]
    bad_json = post_single(b"{")
    assert bad_json.json() == examples_for(single, "400")["invalid_json"]["value"]

    batch = spec["paths"]["/api/v1/validate-batch"]["post"]
    batch_examples = batch["requestBody"]["content"]["application/json"]["examples"]
    response = post_batch(dumps(batch_examples["invalid_batch"]["value"]))
    assert response.json() == examples_for(batch, "422")["invalid_batch"]["value"]
    assert post_batch(dumps(batch_examples["valid_batch"]["value"])).status == 200


def test_documentation_examples_are_the_reference_fixtures() -> None:
    assert json.loads(example_bytes("valid-record.json")) == EXAMPLE_RECORD
    assert json.loads(example_bytes("invalid-batch.json")) == EXAMPLE_INVALID_BATCH


@pytest.mark.parametrize(
    ("fixture", "post", "query"),
    [
        ("valid-record.json", post_single, "echo=true"),
        ("nested-reserved-metadata.json", post_single, ""),
        ("invalid-batch.json", post_batch, ""),
        ("valid-batch.json", post_batch, "echo=true"),
        ("long-diagnostic-path.json", post_single, ""),
    ],
)
def test_runtime_reports_conform_to_response_schema(fixture: str, post: Any, query: str) -> None:
    response = post(example_bytes(fixture), query=query)
    VALIDATION_REPORT_ADAPTER.validate_json(response.body, strict=True)


@pytest.mark.parametrize(
    "response_factory",
    [
        lambda: post_single(b"{"),
        lambda: post_single(b"{}", headers=[]),
        lambda: post_single(example_bytes("valid-record.json"), query="echo=no&x=1"),
        lambda: post_batch(b"[]"),
    ],
)
def test_runtime_failures_conform_to_failure_schema(response_factory: Any) -> None:
    REQUEST_FAILURE_ADAPTER.validate_json(response_factory().body, strict=True)


def matching_variants(body: bytes) -> list[str]:
    matches = []
    for name, adapter in (
        ("ValidationReport", VALIDATION_REPORT_ADAPTER),
        ("RequestFailure", REQUEST_FAILURE_ADAPTER),
    ):
        try:
            adapter.validate_json(body, strict=True)
        except ValidationError:
            continue
        matches.append(name)
    return matches


def test_422_variants_are_mutually_exclusive(spec: dict[str, Any]) -> None:
    assert schemas(spec)["RequestFailure"]["additionalProperties"] is False
    report = post_batch(example_bytes("invalid-batch.json"))
    assert matching_variants(report.body) == ["ValidationReport"]
    for failure in (
        post_single(b"[]"),
        post_single(example_bytes("valid-record.json"), query="x=1"),
    ):
        assert failure.status == 422
        assert matching_variants(failure.body) == ["RequestFailure"]
    for path in POST_PATHS:
        examples = examples_for(spec["paths"][path]["post"], "422")
        for name, example in examples.items():
            expected = "ValidationReport" if "records" in example["value"] else "RequestFailure"
            assert matching_variants(json.dumps(example["value"]).encode()) == [expected], name


def test_request_models_agree_with_runtime_on_reference_fixtures() -> None:
    Record.model_validate_json(example_bytes("valid-record.json"))
    Record.model_validate_json(example_bytes("nested-reserved-metadata.json"))
    BatchEnvelope.model_validate_json(example_bytes("valid-batch.json"))
    with pytest.raises(ValidationError):
        Record.model_validate_json(dumps({**EXAMPLE_RECORD, "annotations": {"ae": []}}))
    with pytest.raises(ValidationError):
        Record.model_validate_json(dumps({**EXAMPLE_RECORD, "case_id": 5001}))
    with pytest.raises(ValidationError):
        Record.model_validate_json(dumps({**EXAMPLE_RECORD, "case_id": "5001"}))
    with pytest.raises(ValidationError):
        BatchEnvelope.model_validate_json(dumps({"records": [EXAMPLE_RECORD], "extra": 1}))


def test_interactive_docs_available() -> None:
    response = call("GET", "/docs")
    assert response.status == 200
    assert b"swagger" in response.body.lower()
