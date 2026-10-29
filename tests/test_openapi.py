"""OpenAPI contract and schema/runtime agreement (V20)."""

import json
import re
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import TypeAdapter, ValidationError

from scat_validator.api import app
from scat_validator.config import LABELS, MAX_SAFE_NUMBER, MODEL_PATTERN, PROVIDER_PATTERN
from scat_validator.contracts import (
    CASE_ID_PATTERN,
    LLM_PROPOSAL_ADAPTER,
    LLM_STATUS_ADAPTER,
    REQUEST_FAILURE_ADAPTER,
    SET_ID_PATTERN,
    SUBMISSION_FAILURE_ADAPTER,
    SUBMISSION_UPDATE_ADAPTER,
    VALIDATION_REPORT_ADAPTER,
    BatchEnvelope,
    Record,
)
from scat_validator.openapi_docs import (
    EXAMPLE_ANNOTATED_RECORD,
    EXAMPLE_INVALID_BATCH,
    EXAMPLE_RECORD,
)
from scat_validator.storage import Storage
from tests.helpers import (
    call,
    delete,
    dumps,
    example_bytes,
    export,
    post_batch,
    post_single,
    submit,
)

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
    assert "annotations" not in record["properties"]  # superseded, rejected at runtime
    sets = record["properties"]["annotation_sets"]
    assert "default" not in sets
    assert (sets["type"], sets["maxProperties"]) == ("object", 16)
    assert sets["propertyNames"] == {"pattern": SET_ID_PATTERN}
    assert sets["additionalProperties"] == {"$ref": "#/components/schemas/AnnotationSet"}
    assert "patternProperties" not in sets
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


def test_annotation_set_schema(spec: dict[str, Any]) -> None:
    assert SET_ID_PATTERN == (
        "^(user|llm:[a-z][a-z0-9-]{0,31}:[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127})$"
    )
    annotation_set = schemas(spec)["AnnotationSet"]
    assert annotation_set["additionalProperties"] is True
    assert annotation_set["required"] == ["kind", "annotations"]
    properties = annotation_set["properties"]
    assert list(properties) == ["kind", "annotations", "provider", "model", "generated_at"]
    assert properties["kind"]["enum"] == ["user", "llm"]
    assert properties["annotations"] == {"$ref": "#/components/schemas/AnnotationMap"}
    # Foreign metadata on a user set, so the schema leaves them unconstrained (llm rules are
    # described and enforced at runtime).
    for name in ("provider", "model", "generated_at"):
        assert set(properties[name]) <= {"description", "examples", "title"}, name
    assert PROVIDER_PATTERN in properties["provider"]["description"]
    assert MODEL_PATTERN in properties["model"]["description"]


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
        "ALREADY_SUBMITTED",
        "STORAGE_UNAVAILABLE",
        "INVALID_PARAMETERS",
        "NOT_FOUND",
        "PRECONDITION_REQUIRED",
        "VERSION_CONFLICT",
        "LLM_NOT_CONFIGURED",
        "LLM_UNAVAILABLE",
        "LLM_TIMEOUT",
        "LLM_OUTPUT_INVALID",
        "NARRATIVE_TOO_LONG",
        "NARRATIVE_HAS_MARKUP",
        "PROPOSAL_NOT_FOUND",
        "SET_NOT_FOUND",
    }
    assert schemas(spec)["Location"]["enum"] == ["body", "query", "path", "llm_output"]


def examples_for(operation: dict[str, Any], status: str) -> dict[str, Any]:
    content = operation["responses"][status]["content"]["application/json"]
    result: dict[str, Any] = content.get("examples", {})
    return result


def test_examples_match_runtime(spec: dict[str, Any]) -> None:
    single = spec["paths"]["/api/v1/validate"]["post"]
    requests = single["requestBody"]["content"]["application/json"]["examples"]
    for name in ("minimal_record", "annotated_record"):
        response = post_single(dumps(requests[name]["value"]))
        assert response.json() == examples_for(single, "200")[name]["value"]
    request = requests["minimal_record"]
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
    assert json.loads(example_bytes("annotated-record.json")) == EXAMPLE_ANNOTATED_RECORD
    assert json.loads(example_bytes("invalid-batch.json")) == EXAMPLE_INVALID_BATCH


def _records_in(example: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = example.get("records", [example])
    return records


def test_documented_request_examples_are_minimal(spec: dict[str, Any]) -> None:
    single = spec["paths"]["/api/v1/validate"]["post"]
    single_examples = single["requestBody"]["content"]["application/json"]["examples"]
    assert list(single_examples) == ["minimal_record", "annotated_record"]
    assert single_examples["minimal_record"]["value"] == {
        "case_id": "12345678-1",
        "username": "demo_annotator",
        "narrative": "Aspirin caused nausea.",
    }
    batch = spec["paths"]["/api/v1/validate-batch"]["post"]
    batch_examples = batch["requestBody"]["content"]["application/json"]["examples"]
    # No foreign fields anywhere: records and sets hold only owned fields, annotations only
    # text/start/end.
    for example in [*single_examples.values(), *batch_examples.values()]:
        for record in _records_in(example["value"]):
            assert set(record) <= {"case_id", "username", "narrative", "annotation_sets"}
            for annotation_set in record.get("annotation_sets", {}).values():
                assert set(annotation_set) == {"kind", "annotations"}
                for items in annotation_set["annotations"].values():
                    assert all(set(item) == {"text", "start", "end"} for item in items)
    for path in POST_PATHS:
        for status in ("200", "422"):
            for example in examples_for(spec["paths"][path]["post"], status).values():
                assert example["value"].get("foreign_field_count", 0) == 0


@pytest.mark.parametrize(
    ("fixture", "post", "query"),
    [
        ("valid-record.json", post_single, "echo=true"),
        ("annotated-record.json", post_single, ""),
        ("metadata-record.json", post_single, "echo=true"),
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
        lambda: post_single(example_bytes("metadata-record.json"), query="echo=no&x=1"),
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
        post_single(example_bytes("metadata-record.json"), query="x=1"),
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
    Record.model_validate_json(example_bytes("annotated-record.json"))
    Record.model_validate_json(example_bytes("metadata-record.json"))
    Record.model_validate_json(example_bytes("nested-reserved-metadata.json"))
    BatchEnvelope.model_validate_json(example_bytes("valid-batch.json"))
    # Provenance-like members of a user set are foreign metadata in both the schema and runtime.
    foreign = {
        "kind": "user",
        "annotations": {},
        "provider": "Anthropic Inc",
        "model": 1,
        "generated_at": "yesterday",
    }
    foreign_record = dumps({**EXAMPLE_RECORD, "annotation_sets": {"user": foreign}})
    Record.model_validate_json(foreign_record)
    assert post_single(foreign_record).status == 200
    user_set = {"kind": "user", "annotations": {"ae": []}}
    with pytest.raises(ValidationError):
        Record.model_validate_json(dumps({**EXAMPLE_RECORD, "annotation_sets": {"user": user_set}}))
    with pytest.raises(ValidationError):
        Record.model_validate_json(
            dumps({**EXAMPLE_RECORD, "annotation_sets": {"User": {**user_set, "annotations": {}}}})
        )
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


# ---------------------------------------------------------------- stage 2 submissions (S09)

SUBMISSIONS = "/api/v1/submissions"
SUBMISSION = "/api/v1/submissions/{case_id}"


def test_submission_operations_document_statuses_and_schemas(spec: dict[str, Any]) -> None:
    post = spec["paths"][SUBMISSIONS]["post"]
    assert post["parameters"] == []
    assert post["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/Record"
    }
    assert set(post["responses"]) == {"201", "400", "409", "413", "415", "422", "500", "503"}
    created = post["responses"]["201"]
    assert "Location" in created["headers"]
    assert created["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/SubmissionReceipt"
    }
    for status in ("409", "503"):
        schema = post["responses"][status]["content"]["application/json"]["schema"]
        assert schema == {"$ref": "#/components/schemas/RequestFailure"}
    assert "no authentication" in post["description"]

    expected_ok = {"get": ("200", "SubmissionExport"), "delete": ("204", None)}
    for method, (ok_status, ok_schema) in expected_ok.items():
        operation = spec["paths"][SUBMISSION][method]
        assert set(operation["responses"]) == {ok_status, "404", "422", "500", "503"}
        ok = operation["responses"][ok_status]
        if ok_schema is None:
            assert "content" not in ok
        else:
            assert ok["content"]["application/json"]["schema"] == {
                "$ref": f"#/components/schemas/{ok_schema}"
            }
        for status in ("404", "422", "500", "503"):
            schema = operation["responses"][status]["content"]["application/json"]["schema"]
            assert schema == {"$ref": "#/components/schemas/SubmissionFailure"}
        case_id, username = operation["parameters"]
        assert (case_id["name"], case_id["in"], case_id["required"]) == ("case_id", "path", True)
        assert case_id["schema"]["pattern"] == CASE_ID_PATTERN
        assert (username["name"], username["in"], username["required"]) == (
            "username",
            "query",
            True,
        )
        assert username["schema"]["maxLength"] == 255
        assert "%2B" in username["description"]
    for path in (SUBMISSIONS, SUBMISSION):
        for operation in spec["paths"][path].values():
            for response in operation["responses"].values():
                assert response["headers"]["Cache-Control"]["schema"]["enum"] == ["no-store"]


def test_submission_failure_is_closed(spec: dict[str, Any]) -> None:
    failure = schemas(spec)["SubmissionFailure"]
    assert failure["additionalProperties"] is False
    assert set(failure["required"]) == {"error", "issues", "issue_count", "omitted_issue_count"}
    export_schema = schemas(spec)["SubmissionExport"]
    assert export_schema["properties"]["submitted_at"]["format"] == "date-time"
    assert export_schema["properties"]["offset_unit"]["const"] == "unicode-code-point"


def test_submission_examples_match_runtime(spec: dict[str, Any], storage: Storage) -> None:
    post = spec["paths"][SUBMISSIONS]["post"]
    request = post["requestBody"]["content"]["application/json"]["examples"]["minimal_record"]
    receipt = submit(dumps(request["value"]))
    documented = examples_for(post, "201")["minimal_record"]["value"]
    assert receipt.json() == documented | {"submitted_at": receipt.json()["submitted_at"]}
    rejected = submit(dumps(request["value"]), query="echo=true")
    assert rejected.json() == examples_for(post, "422")["query_parameter"]["value"]

    get = spec["paths"][SUBMISSION]["get"]
    exported = export("12345678-1", "username=demo_annotator")
    documented = examples_for(get, "200")["minimal_record"]["value"]
    timestamps = {name: exported.json()[name] for name in ("submitted_at", "updated_at")}
    assert exported.json() == documented | timestamps
    missing_username = examples_for(get, "422")["missing_username"]["value"]
    assert export("12345678-1", "").json() == missing_username

    remove = spec["paths"][SUBMISSION]["delete"]
    assert delete("12345678-1", "username=demo_annotator").status == 204
    not_found = delete("12345678-1", "username=demo_annotator")
    assert not_found.json() == examples_for(remove, "404")["not_found"]["value"]


def test_storage_unavailable_example_matches_runtime(spec: dict[str, Any]) -> None:
    get = spec["paths"][SUBMISSION]["get"]
    response = export("12345678-1", "username=demo_annotator")
    assert response.status == 503
    assert response.json() == examples_for(get, "503")["storage_unavailable"]["value"]


# ---------------------------------------------------------------- stage 4 LLM routes (L12)

LLM_OPERATIONS: dict[tuple[str, str], tuple[set[str], list[tuple[str, str]]]] = {
    ("/api/v1/llm", "get"): ({"200", "413", "500"}, []),
    ("/api/v1/submissions/{case_id}/llm-proposals", "post"): (
        {"201", "404", "413", "422", "500", "502", "503", "504"},
        [("case_id", "path"), ("username", "query")],
    ),
    ("/api/v1/submissions/{case_id}/llm-proposals/{proposal_id}/accept", "post"): (
        {"200", "404", "412", "413", "422", "428", "500", "503"},
        [
            ("case_id", "path"),
            ("proposal_id", "path"),
            ("username", "query"),
            ("If-Match", "header"),
        ],
    ),
    ("/api/v1/submissions/{case_id}/llm-proposals/{proposal_id}", "delete"): (
        {"204", "404", "413", "422", "500", "503"},
        [("case_id", "path"), ("proposal_id", "path"), ("username", "query")],
    ),
    ("/api/v1/submissions/{case_id}/annotation-sets", "delete"): (
        {"200", "404", "412", "413", "422", "428", "500", "503"},
        [("case_id", "path"), ("username", "query"), ("set_id", "query"), ("If-Match", "header")],
    ),
}
SCHEMA_ADAPTERS: dict[str, TypeAdapter[Any]] = {
    "LlmStatus": LLM_STATUS_ADAPTER,
    "LlmProposal": LLM_PROPOSAL_ADAPTER,
    "SubmissionFailure": SUBMISSION_FAILURE_ADAPTER,
    "SubmissionUpdate": SUBMISSION_UPDATE_ADAPTER,
    "ValidationReport": VALIDATION_REPORT_ADAPTER,
}


def llm_operation(spec: dict[str, Any], path: str, method: str) -> dict[str, Any]:
    operation: dict[str, Any] = spec["paths"][path][method]
    return operation


def test_llm_operations_are_documented(spec: dict[str, Any]) -> None:
    for (path, method), (statuses, parameters) in LLM_OPERATIONS.items():
        operation = llm_operation(spec, path, method)
        assert set(operation["responses"]) == statuses, path
        assert [(p["name"], p["in"]) for p in operation.get("parameters", [])] == parameters
        assert "requestBody" not in operation
        assert "413" in operation["responses"]
        for response in operation["responses"].values():
            assert response["headers"]["Cache-Control"]["schema"]["enum"] == ["no-store"]
    status = schemas(spec)["LlmStatus"]
    assert list(status["properties"]) == ["enabled", "provider", "model"]
    assert status["additionalProperties"] is False
    proposal = schemas(spec)["LlmProposal"]
    assert list(proposal["properties"]) == [
        "proposal_id",
        "case_id",
        "username",
        "set_id",
        "set",
        "annotation_count",
        "annotations_by_label",
        "replaces_existing_set",
        "based_on_version",
        "expires_at",
    ]
    set_id = llm_operation(spec, "/api/v1/submissions/{case_id}/annotation-sets", "delete")
    pattern = set_id["parameters"][2]["schema"]["pattern"]
    assert re.fullmatch(pattern, "llm:openai:org/m:8b")
    assert not re.fullmatch(pattern, "user")


def _schema_name(schema: dict[str, Any]) -> list[str]:
    if "oneOf" in schema:
        return [branch["$ref"].rsplit("/", 1)[1] for branch in schema["oneOf"]]
    return [schema["$ref"].rsplit("/", 1)[1]]


def test_llm_examples_validate_against_schemas(spec: dict[str, Any]) -> None:
    seen = 0
    for path, method in LLM_OPERATIONS:
        for response in llm_operation(spec, path, method)["responses"].values():
            content = response.get("content", {}).get("application/json")
            if content is None:
                continue
            names = _schema_name(content["schema"])
            for example in content.get("examples", {}).values():
                body = json.dumps(example["value"])
                matches = []
                for name in names:
                    try:
                        SCHEMA_ADAPTERS[name].validate_json(body, strict=True)
                    except ValidationError:
                        continue
                    matches.append(name)
                assert len(matches) == 1, (path, example)
                seen += 1
    assert seen >= 20


def test_runtime_llm_responses_conform(
    spec: dict[str, Any], storage: Storage, llm: Callable[[Any], Any]
) -> None:
    llm("<SDRUG>Aspirin</SDRUG> caused nausea.")
    state = app.state
    status_op = llm_operation(spec, "/api/v1/llm", "get")
    status = call("GET", "/api/v1/llm", headers=[])
    assert status.json() == examples_for(status_op, "200")["enabled"]["value"]
    settings = state.llm_settings
    state.llm_settings = None
    disabled = call("GET", "/api/v1/llm", headers=[])
    assert disabled.json() == examples_for(status_op, "200")["disabled"]["value"]
    proposals = "/api/v1/submissions/12345678-1/llm-proposals"
    propose_op = llm_operation(spec, "/api/v1/submissions/{case_id}/llm-proposals", "post")
    not_configured = call("POST", proposals, query="username=demo_annotator", headers=[])
    assert not_configured.json() == examples_for(propose_op, "503")["not_configured"]["value"]
    state.llm_settings = settings

    query = "username=demo_annotator"
    not_found = call("POST", proposals, query=query, headers=[])
    assert not_found.json() == examples_for(propose_op, "404")["not_found"]["value"]
    assert submit(dumps(EXAMPLE_RECORD)).status == 201
    created = call("POST", proposals, query=query, headers=[])
    assert created.status == 201
    LLM_PROPOSAL_ADAPTER.validate_json(created.body, strict=True)
    body = created.json()
    documented = examples_for(propose_op, "201")["proposal"]["value"]
    assert body == documented | {name: body[name] for name in ("proposal_id", "expires_at")}

    llm("Aspirin caused nausea!")
    altered = call("POST", proposals, query=query, headers=[])
    assert altered.json() == examples_for(propose_op, "502")["narrative_altered"]["value"]
    llm(lambda request: httpx.Response(503))
    unavailable = call("POST", proposals, query=query, headers=[])
    assert unavailable.json() == examples_for(propose_op, "502")["provider_unavailable"]["value"]

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    llm(timeout)
    timed_out = call("POST", proposals, query=query, headers=[])
    assert timed_out.json() == examples_for(propose_op, "504")["timeout"]["value"]

    accept_path = "/api/v1/submissions/{case_id}/llm-proposals/{proposal_id}/accept"
    accept_op = llm_operation(spec, accept_path, "post")
    proposal = f"{proposals}/{body['proposal_id']}"
    bad_id = call("POST", f"{proposals}/nope/accept", query=query, headers=[])
    assert bad_id.json() == examples_for(accept_op, "422")["invalid_proposal_id"]["value"]
    missing = call("POST", f"{proposal}/accept", query=query, headers=[])
    assert missing.json() == examples_for(accept_op, "428")["precondition_required"]["value"]
    if_match = [(b"if-match", b'"1"')]
    accepted = call("POST", f"{proposal}/accept", query=query, headers=if_match)
    assert accepted.status == 200
    timestamps = {name: accepted.json()[name] for name in ("submitted_at", "updated_at")}
    assert accepted.json() == examples_for(accept_op, "200")["saved"]["value"] | timestamps
    again = call("POST", f"{proposal}/accept", query=query, headers=[(b"if-match", b'"2"')])
    assert again.json() == examples_for(accept_op, "404")["proposal_not_found"]["value"]
    stale = call("POST", f"{proposal}/accept", query=query, headers=if_match)
    assert stale.json() == examples_for(accept_op, "404")["proposal_not_found"]["value"]

    discard_op = llm_operation(
        spec, "/api/v1/submissions/{case_id}/llm-proposals/{proposal_id}", "delete"
    )
    gone = call("DELETE", proposal, query=query, headers=[])
    assert gone.json() == examples_for(discard_op, "404")["proposal_not_found"]["value"]

    sets_op = llm_operation(spec, "/api/v1/submissions/{case_id}/annotation-sets", "delete")
    sets = "/api/v1/submissions/12345678-1/annotation-sets"
    set_query = query + "&set_id=llm%3Aanthropic%3Aclaude-x"
    user = call("DELETE", sets, query=query + "&set_id=user", headers=[])
    assert user.json() == examples_for(sets_op, "422")["user_set_id"]["value"]
    conflict = call("DELETE", sets, query=set_query, headers=if_match)
    assert conflict.json() == examples_for(sets_op, "412")["version_conflict"]["value"]
    removed = call("DELETE", sets, query=set_query, headers=[(b"if-match", b'"2"')])
    assert removed.status == 200
    SUBMISSION_UPDATE_ADAPTER.validate_json(removed.body, strict=True)
    timestamps = {name: removed.json()[name] for name in ("submitted_at", "updated_at")}
    assert removed.json() == examples_for(sets_op, "200")["saved"]["value"] | timestamps
    absent = call("DELETE", sets, query=set_query, headers=[(b"if-match", b'"3"')])
    assert absent.json() == examples_for(sets_op, "404")["set_not_found"]["value"]
    for response in (not_found, altered, unavailable, timed_out, bad_id, again, gone, absent):
        SUBMISSION_FAILURE_ADAPTER.validate_json(response.body, strict=True)
