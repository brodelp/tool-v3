"""Smoke test against a real listening SCAT server (local uvicorn or Docker Compose).

Usage: python scripts/smoke_test.py [BASE_URL]   (default http://127.0.0.1:8000)

Standard library only, so it also runs outside the project environment. Responses are checked
as raw bytes where numeric fidelity matters.
"""

import http.client
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

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
        and report["issues"][0]["path"] == "/records/1/annotations/AE/0/text"
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

    status, _, body = server.request("GET", "/openapi.json")
    spec = json.loads(body)
    check(
        "GET /openapi.json documents both POST endpoints",
        status == 200 and {"/api/v1/validate", "/api/v1/validate-batch"} <= set(spec["paths"]),
    )

    status, _, body = server.request("GET", "/docs")
    check("GET /docs -> interactive documentation", status == 200 and b"swagger" in body.lower())

    print(f"{len(checks)} smoke checks passed")


if __name__ == "__main__":
    main()
