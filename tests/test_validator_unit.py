"""Pure validator, path, and gate units without HTTP (supports V02-V19)."""

import subprocess
import sys

import pytest

from scat_validator.diagnostics import InventoryCollector, IssueCollector
from scat_validator.http_gates import (
    content_encoding_supported,
    declared_length_too_large,
    media_type_supported,
    parse_query,
)
from scat_validator.paths import PathNode, render
from scat_validator.validation import (
    RecordResult,
    UsernameProblem,
    username_problem,
    validate_record,
)
from scat_validator.wire import decode_document
from tests.helpers import example_bytes


def run(raw: bytes) -> tuple[IssueCollector, InventoryCollector, RecordResult]:
    issues, inventory = IssueCollector(), InventoryCollector()
    result = validate_record(decode_document(raw).value, PathNode.root(), issues, inventory, set())
    return issues, inventory, result


def test_validator_runs_without_http_and_reports_reference_record() -> None:
    issues, inventory, result = run(example_bytes("valid-record.json"))
    assert issues.count == 0
    assert inventory.count == 2
    assert result.valid is True


def test_validation_module_has_no_http_dependency() -> None:
    code = (
        "import sys, scat_validator.validation, scat_validator.reporting;"
        "bad=[m for m in sys.modules if m.split('.')[0] in ('fastapi','starlette')];"
        "print(','.join(bad))"
    )
    output = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert output.stdout.strip() == ""


def test_shared_identity_set_is_request_local() -> None:
    raw = decode_document(example_bytes("valid-record.json")).value
    seen: set[tuple[int, int]] = set()
    issues, inventory = IssueCollector(), InventoryCollector()
    first = validate_record(raw, PathNode.root(), issues, inventory, seen)
    second = validate_record(raw, PathNode.root(), issues, inventory, seen)
    assert first.valid and not second.valid
    assert second.checks["record_identity_uniqueness"] == "failed"
    third = validate_record(raw, PathNode.root(), IssueCollector(), InventoryCollector(), set())
    assert third.valid


def test_collector_counts_beyond_cap_without_retaining() -> None:
    collector = IssueCollector(cap=2)
    node = PathNode.root().child("x")
    for _ in range(5):
        collector.add("body", node, "INVALID_TYPE", "m")
    assert collector.count == 5 and len(collector.items) == 2


def test_render_whole_token_fallbacks() -> None:
    root = PathNode.root()
    assert render(root).pointer == ""
    node = root.child("a/b").child("~").child("")
    assert render(node).pointer == "/a~1b/~0/"
    long_node = root.child("records").child("0").child("x" * 2000).child("y")
    rendered = render(long_node)
    assert (rendered.pointer, rendered.length_fallback, rendered.name_fallback) == (
        "/records/0",
        True,
        False,
    )
    bad = root.child("ok").child("\x00").child("deep")
    rendered = render(bad)
    assert (rendered.pointer, rendered.length_fallback, rendered.name_fallback) == (
        "/ok",
        False,
        True,
    )


def test_path_lengths_are_computed_once_per_node() -> None:
    parent = PathNode.root().child("é" * 600)
    assert parent.byte_length == 1 + 1200
    child = parent.child("~")
    assert child.byte_length == parent.byte_length + 3
    assert not child.is_exactly_representable


@pytest.mark.parametrize(
    ("value", "problem"),
    [
        ("demo_annotator", None),
        ("Brandon.Penny+review@example.com", None),
        ("o'brien@example.com", None),
        ("", UsernameProblem.EMPTY),
        ("　", UsernameProblem.EMPTY),
        ("._+-", UsernameProblem.INVALID),
        ("a" * 256, UsernameProblem.INVALID),
        ("a\u00a0b", UsernameProblem.INVALID),
        ("é@example.com", UsernameProblem.INVALID),
    ],
)
def test_username_profile(value: str, problem: UsernameProblem | None) -> None:
    assert username_problem(value) is problem


def test_gate_helpers() -> None:
    assert media_type_supported(['application/json; charset="utf\\-8"'])
    assert not media_type_supported(['application/json; charset="utf-8'])
    assert content_encoding_supported([])
    assert content_encoding_supported(["identity", "IDENTITY"])
    assert not content_encoding_supported(["identity", "gzip"])
    assert declared_length_too_large(["5242881"])
    assert declared_length_too_large(["0005242881"])
    assert not declared_length_too_large(["5242880"])
    assert not declared_length_too_large(["abc"])
    assert not declared_length_too_large(["1", "9999999"])
    options = parse_query(b"echo=Y")
    assert not isinstance(options, IssueCollector) and options.echo is True
    assert isinstance(parse_query(b"echo=%C4%B0"), IssueCollector)  # no Unicode case folding
