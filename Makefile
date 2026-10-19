# Named commands; run inside the dev container (terminal at /app).
# Compose commands run on the host; see README.md.

.PHONY: fmt lint typecheck test check run smoke lock

fmt:
	uv run --locked ruff format .
	uv run --locked ruff check --fix .

lint:
	uv run --locked ruff format --check .
	uv run --locked ruff check .

typecheck:
	uv run --locked mypy

test:
	uv run --locked pytest

check: lint typecheck test

# --no-access-log: uvicorn's access log would record query values; the app logs route,
# status, duration, and counts instead.
run:
	uv run --locked uvicorn scat_validator.api:app --host 0.0.0.0 --port 8000 --reload --no-access-log

# Against a running server (make run, or docker compose up on the host).
BASE_URL ?= http://127.0.0.1:8000
smoke:
	uv run --locked python scripts/smoke_test.py $(BASE_URL)

lock:
	uv lock
