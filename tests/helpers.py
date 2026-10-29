"""Raw-bytes ASGI client and record builders shared by the tests.

The client drives the real application object with exact request bytes (optionally as several
streamed chunks without Content-Length), so decoding is never bypassed or mocked.
"""

import asyncio
import copy
import json
from collections.abc import Iterable, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from scat_validator.api import app

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
RAW_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "raw"

JSON_HEADERS = [(b"content-type", b"application/json")]


@dataclass
class RawResponse:
    status: int
    headers: list[tuple[bytes, bytes]]
    body: bytes
    receive_calls: int = 0
    chunks_offered: int = 0
    _parsed: Any = field(default=None, repr=False)

    def header(self, name: str) -> str | None:
        wanted = name.lower().encode()
        values = [v.decode("latin-1") for k, v in self.headers if k.lower() == wanted]
        return values[0] if values else None

    def json(self) -> Any:
        if self._parsed is None:
            self._parsed = json.loads(self.body)
        return self._parsed


def call(
    method: str,
    path: str,
    *,
    body: bytes = b"",
    query: bytes | str = b"",
    headers: Iterable[tuple[bytes, bytes]] | None = None,
    chunks: list[bytes] | None = None,
    content_length: bool = True,
) -> RawResponse:
    """Send one request through the ASGI app in a new event loop (see ``call_async``)."""
    return asyncio.run(
        call_async(
            method,
            path,
            body=body,
            query=query,
            headers=headers,
            chunks=chunks,
            content_length=content_length,
        )
    )


async def call_async(
    method: str,
    path: str,
    *,
    body: bytes = b"",
    query: bytes | str = b"",
    headers: Iterable[tuple[bytes, bytes]] | None = None,
    chunks: list[bytes] | None = None,
    content_length: bool = True,
) -> RawResponse:
    """Send one request through the ASGI app in the running event loop.

    With ``chunks`` the body is streamed as separate ``http.request`` messages; with
    ``content_length=False`` no Content-Length header is added (as for chunked uploads).
    """
    header_list = list(JSON_HEADERS if headers is None else headers)
    parts = [body] if chunks is None else chunks
    if content_length and chunks is None:
        header_list.append((b"content-length", str(len(body)).encode()))
    query_bytes = query.encode() if isinstance(query, str) else query
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query_bytes,
        "root_path": "",
        "headers": header_list,
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8000),
    }
    state = {"index": 0, "calls": 0}
    sent: list[MutableMapping[str, Any]] = []

    async def receive() -> dict[str, Any]:
        state["calls"] += 1
        index = state["index"]
        if index < len(parts):
            state["index"] += 1
            return {
                "type": "http.request",
                "body": parts[index],
                "more_body": index + 1 < len(parts),
            }
        await asyncio.sleep(3600)  # pragma: no cover - the app never waits past the body
        return {"type": "http.disconnect"}  # pragma: no cover

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    await app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return RawResponse(
        status=start["status"],
        headers=list(start["headers"]),
        body=payload,
        receive_calls=state["calls"],
        chunks_offered=len(parts),
    )


def post_single(body: bytes | str, query: bytes | str = b"", **kwargs: Any) -> RawResponse:
    raw = body.encode() if isinstance(body, str) else body
    return call("POST", "/api/v1/validate", body=raw, query=query, **kwargs)


def post_batch(body: bytes | str, query: bytes | str = b"", **kwargs: Any) -> RawResponse:
    raw = body.encode() if isinstance(body, str) else body
    return call("POST", "/api/v1/validate-batch", body=raw, query=query, **kwargs)


def example_bytes(name: str) -> bytes:
    return (EXAMPLES / name).read_bytes()


def raw_fixture(name: str) -> bytes:
    return (RAW_FIXTURES / name).read_bytes()


# Raw fixtures predate annotation sets and stay byte-exact. A test whose subject is not the
# annotations (BOMs, numeric tokens) can rewrite this block in memory to the user set.
LEGACY_FIXTURE_ANNOTATIONS = (
    b'  "annotations": {\n'
    b'    "SDrug": [\n'
    b'      {"text": "Aspirin", "start": 0, "end": 7, "external_id": "ann-001"}\n'
    b"    ],\n"
    b'    "AE": [\n'
    b'      {"text": "nausea", "start": 15, "end": 21}\n'
    b"    ]\n"
    b"  },\n"
)
FIXTURE_ANNOTATION_SETS = (
    b'  "annotation_sets": {\n'
    b'    "user": {\n'
    b'      "kind": "user",\n'
    b'      "annotations": {\n'
    b'        "SDrug": [\n'
    b'          {"text": "Aspirin", "start": 0, "end": 7, "external_id": "ann-001"}\n'
    b"        ],\n"
    b'        "AE": [\n'
    b'          {"text": "nausea", "start": 15, "end": 21}\n'
    b"        ]\n"
    b"      }\n"
    b"    }\n"
    b"  },\n"
)


def annotation_sets_fixture(name: str) -> bytes:
    """A raw fixture with its legacy ``annotations`` block moved into the user set."""
    raw = raw_fixture(name)
    assert raw.count(LEGACY_FIXTURE_ANNOTATIONS) == 1
    return raw.replace(LEGACY_FIXTURE_ANNOTATIONS, FIXTURE_ANNOTATION_SETS)


BASE_RECORD: dict[str, Any] = {
    "case_id": "5001-1",
    "username": "demo_annotator",
    "narrative": "Aspirin caused nausea.",
    "annotation_sets": {
        "user": {
            "kind": "user",
            "annotations": {
                "SDrug": [{"text": "Aspirin", "start": 0, "end": 7, "external_id": "ann-001"}],
                "AE": [{"text": "nausea", "start": 15, "end": 21}],
            },
        }
    },
    "source_metadata": {
        "source_record_id": "external-5001",
        "reviewed": False,
        "tags": ["fictional", "validation-demo"],
        "optional_value": None,
    },
}

_MISSING = object()


def make_record(**overrides: Any) -> dict[str, Any]:
    """A deep copy of the §8.1 record; pass ``field=MISSING`` to delete a property."""
    record = copy.deepcopy(BASE_RECORD)
    for key, value in overrides.items():
        if value is _MISSING:
            record.pop(key, None)
        else:
            record[key] = value
    return record


MISSING: Any = _MISSING


def user_set(annotations: Any) -> dict[str, Any]:
    return {"kind": "user", "annotations": annotations}


def with_user_annotations(annotations: Any, **overrides: Any) -> dict[str, Any]:
    """The base record whose only annotation set is the user set holding ``annotations``."""
    return make_record(annotation_sets={"user": user_set(annotations)}, **overrides)


def dumps(value: Any) -> bytes:
    """Serialize test-built values that contain no precision-sensitive numbers.

    Raw UTF-8 where possible; lone surrogates can only be sent as JSON escapes.
    """
    try:
        return json.dumps(value, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        return json.dumps(value).encode("ascii")


def issues_of(response: RawResponse) -> list[tuple[str, str]]:
    return [(i["path"], i["code"]) for i in response.json()["issues"]]


def issue_triples(response: RawResponse) -> list[tuple[str, str, str]]:
    return [(i["location"], i["path"], i["code"]) for i in response.json()["issues"]]


def checks_of(response: RawResponse, index: int = 0) -> dict[str, str]:
    checks: dict[str, str] = response.json()["records"][index]["checks"]
    return checks


def submit(body: bytes | str, query: bytes | str = b"", **kwargs: Any) -> RawResponse:
    raw = body.encode() if isinstance(body, str) else body
    return call("POST", "/api/v1/submissions", body=raw, query=query, **kwargs)


def username_query(username: str) -> str:
    return "username=" + quote(username, safe="")


def export(case_id: str, query: bytes | str) -> RawResponse:
    """GET a submission; ``query`` is the raw query string (see :func:`username_query`)."""
    return call("GET", f"/api/v1/submissions/{case_id}", query=query, headers=[])


def delete(case_id: str, query: bytes | str) -> RawResponse:
    return call("DELETE", f"/api/v1/submissions/{case_id}", query=query, headers=[])


def list_submissions(query: bytes | str) -> RawResponse:
    return call("GET", "/api/v1/submissions", query=query, headers=[])


def update_annotations(
    case_id: str,
    query: bytes | str,
    body: bytes,
    if_match: str | None = '"1"',
    **kwargs: Any,
) -> RawResponse:
    """PUT an annotation map; ``if_match=None`` sends no If-Match header."""
    headers = list(kwargs.pop("headers", JSON_HEADERS))
    if if_match is not None:
        headers.append((b"if-match", if_match.encode("latin-1")))
    path = f"/api/v1/submissions/{case_id}/annotation-sets/user"
    return call("PUT", path, body=body, query=query, headers=headers, **kwargs)
