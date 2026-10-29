"""Smoke test against a real listening SCAT server (local uvicorn or Docker Compose).

Usage: python scripts/smoke_test.py [BASE_URL]   (default http://127.0.0.1:8000)

Standard library only, so it also runs outside the project environment. Responses are checked
as raw bytes where numeric fidelity matters.
"""

import http.client
import json
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


class Server:
    def __init__(self, base_url: str) -> None:
        parts = urlsplit(base_url)
        self.host = parts.hostname or "127.0.0.1"
        self.port = parts.port or 80

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        *,
        chunked: list[bytes] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=30)
        try:
            if chunked is not None:
                connection.putrequest(method, path)
                for name, value in (headers or {}).items():
                    connection.putheader(name, value)
                connection.putheader("Transfer-Encoding", "chunked")
                connection.endheaders()
                try:
                    for chunk in chunked:
                        connection.send(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                    connection.send(b"0\r\n\r\n")
                except BrokenPipeError, ConnectionResetError:
                    pass  # the server may answer 413 and close before the upload ends
            else:
                connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return (
                response.status,
                {k.lower(): v for k, v in response.getheaders()},
                response.read(),
            )
        finally:
            connection.close()


JSON = {"Content-Type": "application/json"}
checks: list[str] = []


def check(name: str, condition: bool) -> None:
    checks.append(name)
    status = "ok  " if condition else "FAIL"
    print(f"[{status}] {name}")
    if not condition:
        raise SystemExit(1)


def main() -> None:
    server = Server(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000")
    valid = (EXAMPLES / "valid-record.json").read_bytes()
    invalid_batch = (EXAMPLES / "invalid-batch.json").read_bytes()
    numeric = (EXAMPLES / "exact-numeric-metadata.json").read_bytes()

    status, _, body = server.request("GET", "/healthz")
    check('GET /healthz -> 200 {"status":"ok"}', status == 200 and body == b'{"status":"ok"}')

    status, headers, body = server.request("POST", "/api/v1/validate", valid, JSON)
    report = json.loads(body)
    check("valid record -> 200 valid report", status == 200 and report["valid"] is True)
    check(
        "no-store and application/json headers",
        headers.get("cache-control") == "no-store"
        and headers.get("content-type") == "application/json",
    )
    check("no echo by default", "echo" not in report)

    status, _, body = server.request("POST", "/api/v1/validate?echo=true", valid, JSON)
    check(
        "echo=true -> exact source embedded",
        status == 200 and body.endswith(b',"echo":' + valid.strip() + b"}"),
    )

    status, _, body = server.request("POST", "/api/v1/validate?echo=true", numeric, JSON)
    tokens = [b"0.100000000000000005", b"1e-400", b": -0,", b"1.2300", b"1e0", b"1.0,"]
    check(
        "numeric tokens survive echo byte-for-byte",
        status == 200 and all(token in body for token in tokens),
    )

    status, _, body = server.request("POST", "/api/v1/validate-batch", invalid_batch, JSON)
    report = json.loads(body)
    check(
        "invalid batch -> 422 with one TEXT_MISMATCH",
        status == 422
        and report["issue_count"] == 1
        and report["issues"][0]["path"] == "/records/1/annotation_sets/user/annotations/AE/0/text"
        and (report["record_count"], report["valid_record_count"]) == (2, 1),
    )

    status, _, body = server.request("POST", "/api/v1/validate?echo=maybe", valid, JSON)
    check(
        "echo=maybe -> 422 INVALID_OPTIONS",
        status == 422 and json.loads(body)["error"]["code"] == "INVALID_OPTIONS",
    )

    status, _, body = server.request(
        "POST", "/api/v1/validate", valid, {"Content-Type": "text/plain"}
    )
    check(
        "wrong media type -> 415",
        status == 415 and json.loads(body)["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE",
    )

    status, _, body = server.request("POST", "/api/v1/validate", b"{bad", JSON)
    check("malformed JSON -> 400 INVALID_JSON", status == 400)

    chunks = [b"[" + b" " * 65535] + [b" " * 65536] * 100
    status, _, body = server.request("POST", "/api/v1/validate", headers=JSON, chunked=chunks)
    check(
        "streamed oversized body without Content-Length -> 413",
        status == 413 and json.loads(body)["error"]["code"] == "PAYLOAD_TOO_LARGE",
    )

    llm_status(server)
    submissions(server)

    status, _, body = server.request("GET", "/openapi.json")
    spec = json.loads(body)
    documented = {
        "/api/v1/validate",
        "/api/v1/validate-batch",
        "/api/v1/submissions",
        "/api/v1/submissions/{case_id}",
        "/api/v1/submissions/{case_id}/annotation-sets/user",
        "/api/v1/submissions/{case_id}/annotation-sets",
        "/api/v1/submissions/{case_id}/llm-proposals",
        "/api/v1/submissions/{case_id}/llm-proposals/{proposal_id}",
        "/api/v1/submissions/{case_id}/llm-proposals/{proposal_id}/accept",
        "/api/v1/llm",
    }
    check(
        "GET /openapi.json documents every endpoint",
        status == 200 and documented <= set(spec["paths"]),
    )

    status, _, body = server.request("GET", "/docs")
    check("GET /docs -> interactive documentation", status == 200 and b"swagger" in body.lower())

    print(f"{len(checks)} smoke checks passed")


def llm_status(server: Server) -> None:
    """GET /api/v1/llm: disabled unless SCAT_LLM_PROVIDER is set here; never secrets."""
    status, headers, body = server.request("GET", "/api/v1/llm")
    state = json.loads(body) if status == 200 else {}
    check(
        "GET /api/v1/llm -> 200 {enabled, provider, model} only",
        headers.get("cache-control") == "no-store"
        and list(state) == ["enabled", "provider", "model"],
    )
    if os.environ.get("SCAT_LLM_PROVIDER"):
        check("LLM annotation enabled", state["enabled"] is True and bool(state["model"]))
    else:
        check(
            "LLM annotation disabled without configuration",
            state == {"enabled": False, "provider": None, "model": None},
        )


def submissions(server: Server) -> None:
    """Submit, export, list, update, and delete one record under a fresh case_id.

    Needs a migrated database.
    """
    case_id = f"smoke{secrets.token_hex(4)}-1"
    username = "smoke+test@example.com"
    record = (
        b'{"case_id": "%s", "username": "%s", "narrative": "Aspirin caused nausea.",'
        b' "dose_mg": 1.2300, "annotation_sets": {"user": {"kind": "user", "annotations":'
        b' {"AE": [{"text": "nausea", "start": 15, "end": 21}]}}}}'
    ) % (case_id.encode(), username.encode())
    lookup = f"/api/v1/submissions/{case_id}?username={quote(username, safe='')}"

    status, headers, body = server.request("POST", "/api/v1/submissions", record, JSON)
    check(
        "submission -> 201 receipt with export Location (503 means: run the migrations)",
        status == 201 and headers.get("location") == lookup,
    )
    status, _, body = server.request("POST", "/api/v1/submissions", record, JSON)
    check(
        "repeated (case_id, username) -> 409 ALREADY_SUBMITTED",
        status == 409 and json.loads(body)["error"]["code"] == "ALREADY_SUBMITTED",
    )
    status, headers, body = server.request("GET", lookup)
    check(
        "export -> 200 with the exact record (numeric tokens intact)",
        status == 200
        and headers.get("cache-control") == "no-store"
        and body.endswith(b',"record":' + record + b"}"),
    )
    check("export ETag names version 1", headers.get("etag") == '"1"')

    listing_path = f"/api/v1/submissions?username={quote(username, safe='')}"
    status, _, body = server.request("GET", listing_path)
    listed = (
        [entry["case_id"] for entry in json.loads(body)["submissions"]] if status == 200 else []
    )
    check("list -> 200 including the submission", case_id in listed)

    update_path = (
        f"/api/v1/submissions/{case_id}/annotation-sets/user?username={quote(username, safe='')}"
    )
    new_map = b'{"SDrug":[{"text":"Aspirin","start":0,"end":7,"weight":1.0E2}]}'
    update = b'{"annotations":' + new_map + b"}"
    status, headers, _ = server.request("PUT", update_path, update, JSON | {"If-Match": '"1"'})
    check(
        "annotation update -> 200 with ETag version 2",
        status == 200 and headers.get("etag") == '"2"',
    )
    status, _, body = server.request("PUT", update_path, update, JSON | {"If-Match": '"1"'})
    check(
        "stale If-Match -> 412 VERSION_CONFLICT",
        status == 412 and json.loads(body)["error"]["code"] == "VERSION_CONFLICT",
    )
    status, _, body = server.request("GET", lookup)
    check(
        "export after update -> only the user map replaced, byte for byte",
        status == 200
        and b'"dose_mg": 1.2300, "annotation_sets": {"user": {"kind": "user", "annotations": '
        + new_map
        + b"}}}}"
        in body,
    )

    status, _, body = server.request("DELETE", lookup)
    check("delete -> 204 without a body", status == 204 and body == b"")
    status, _, _ = server.request("GET", lookup)
    check("export after delete -> 404", status == 404)


if __name__ == "__main__":
    main()
