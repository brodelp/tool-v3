"""HTTP routes for the SCAT validation API."""

from fastapi import FastAPI

app = FastAPI(title="SCAT validation API", version="0.1.0")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness only; performs no dependency checks."""
    return {"status": "ok"}
