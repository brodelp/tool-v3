# CLAUDE.md

## Environment

- Development happens in the dev container (`.devcontainer/`): Linux, user `vscode`, source
  bind-mounted at `/app`, virtualenv at `/opt/venv`. Run Python, uv, and `make` targets there.
- Compose commands (`docker compose up`, `docker compose down`) run on the Windows host, not in the
  container. See README.md.
- Compose also runs PostgreSQL 18.6 as service `db` (no host port). Only the stage 2 submission
  routes use it, through `src/scat_validator/storage.py` (reads `DATABASE_URL`). Validation,
  reporting, and the `/validate` routes must stay database-free. In the dev container, `psql`
  connects via the `PG*` variables; there is no Docker CLI, so starting, stopping, or resetting the
  database happens on the host (README.md, Database).
- Schema changes are Alembic revisions in `src/scat_validator/migrations/versions/`; keep them in
  step with the `submissions` table in `storage.py` (a test runs `alembic check`).
- Runtimes and dependencies are pinned (`Dockerfile`, `pyproject.toml`, `uv.lock`). Don't bump a
  version or re-lock unless asked; after editing dependencies, run `make lock` and commit `uv.lock`.

## Commands

- `make check` (lint + typecheck + test) must pass before a change is done.
- `make fmt` formats and auto-fixes; `make test` and `make typecheck` run one stage alone.
- `make run` starts the API with reload on port 8000; `make smoke` exercises a running server.
- `make migrate` applies migrations to the dev database. Tests need the `db` service: they create,
  migrate, and drop a throwaway database and never touch the dev data.

## Rules

- Never rewrite files under `tests/fixtures/raw/`: they are byte-exact fixtures (BOMs, CRLF,
  malformed UTF-8, exact numeric tokens). Create new fixtures with explicit bytes, not an editor.
- Files are LF-only (`.gitattributes`). Fixture JSON is UTF-8 without a BOM.
- Never log request bodies, values, query values, or exception messages (see README.md, Logging).
- `src/scat_validator/config.py` is the single source for limits, labels, and check names.
- The specs are `docs/scat-validation-endpoint-spec.md` (stage 1, validation) and
  `docs/scat-stage-2-submission-spec.md` (stage 2, storage/export/delete, which extends stage 1).
  Choices they leave open are recorded in `docs/implementation-decisions.md`, and acceptance
  criteria map to tests in `docs/acceptance-mapping.md`. Update those when behavior changes.
- Never log `case_id` or `username` values, SQL parameters, or database exception messages.
