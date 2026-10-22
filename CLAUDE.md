# CLAUDE.md

## Environment

- Development happens in the dev container (`.devcontainer/`): Linux, user `vscode`, source
  bind-mounted at `/app`, virtualenv at `/opt/venv`. Run Python, uv, and `make` targets there.
- Compose commands (`docker compose up`, `docker compose down`) run on the Windows host, not in the
  container. See README.md.
- Compose also runs PostgreSQL 18.6 as service `db` (no host port). Stage 1 code must not connect to
  it. In the dev container, `psql` connects via the `PG*` variables; there is no Docker CLI, so
  starting, stopping, or resetting the database happens on the host (README.md, Database).
- Runtimes and dependencies are pinned (`Dockerfile`, `pyproject.toml`, `uv.lock`). Don't bump a
  version or re-lock unless asked; after editing dependencies, run `make lock` and commit `uv.lock`.

## Commands

- `make check` (lint + typecheck + test) must pass before a change is done.
- `make fmt` formats and auto-fixes; `make test` and `make typecheck` run one stage alone.
- `make run` starts the API with reload on port 8000; `make smoke` exercises a running server.

## Rules

- Never rewrite files under `tests/fixtures/raw/`: they are byte-exact fixtures (BOMs, CRLF,
  malformed UTF-8, exact numeric tokens). Create new fixtures with explicit bytes, not an editor.
- Files are LF-only (`.gitattributes`). Fixture JSON is UTF-8 without a BOM.
- Never log request bodies, values, query values, or exception messages (see README.md, Logging).
- `src/scat_validator/config.py` is the single source for limits, labels, and check names.
- The spec is `docs/scat-validation-endpoint-spec.md`; choices it leaves open are recorded in
  `docs/implementation-decisions.md`, and acceptance criteria map to tests in
  `docs/acceptance-mapping.md`. Update those when behavior changes.
