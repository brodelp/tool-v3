"""Smoke test against a real listening uvicorn process (V01, V12, V22)."""

import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def test_smoke_script_against_real_server() -> None:
    port = free_port()
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "scat_validator.api:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--no-access-log",
    ]
    server = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1):
                    break
            except OSError:
                assert time.monotonic() < deadline, "server did not start"
                time.sleep(0.1)
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "smoke_test.py"), f"http://127.0.0.1:{port}"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "smoke checks passed" in result.stdout
    finally:
        server.terminate()
        output, _ = server.communicate(timeout=10)
    # Server logs carry route/status/counts only: no query strings or payload values.
    assert b"echo=" not in output
    assert b"demo_annotator" not in output
