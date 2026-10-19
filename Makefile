# Named commands; run inside the dev container (terminal at /app).
# Compose commands run on the host; see README.md.

.PHONY: fmt lint typecheck test check run lock

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

run:
	uv run --locked uvicorn scat_validator.api:app --host 0.0.0.0 --port 8000 --reload

lock:
	uv lock
