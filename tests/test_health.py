from fastapi.testclient import TestClient

from scat_validator.api import app


def test_healthz_returns_exact_body() -> None:
    response = TestClient(app).get("/healthz")

    assert response.status_code == 200
    assert response.content == b'{"status":"ok"}'
