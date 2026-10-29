# CLAUDE.md

## Environment

- Development happens in the dev container (`.devcontainer/`): Linux, user `vscode`, source
  bind-mounted at `/app`, virtualenv at `/opt/venv`. Run Python, uv, and `make` targets there.
- Compose commands (`docker compose up`, `docker compose down`) run on the Windows host, not in the
  container. See README.md.
- Compose also runs PostgreSQL 18.6 as service `db` (no host port). Only the submission routes
  (stages 2 and 3) use it, through `src/scat_validator/storage.py` (reads `DATABASE_URL`). Validation,
  reporting, and the `/validate` routes must stay database-free. In the dev container, `psql`
  connects via the `PG*` variables; there is no Docker CLI, so starting, stopping, or resetting the
  database happens on the host (README.md, Database).
- Schema changes are Alembic revisions in `src/scat_validator/migrations/versions/`; keep them in
  step with the `submissions` table in `storage.py` (a test runs `alembic check`).
- Runtimes and dependencies are pinned (`Dockerfile`, `pyproject.toml`, `uv.lock`,
  `frontend/package.json`, `frontend/package-lock.json`). Don't bump a version or re-lock unless
  asked; after editing Python dependencies, run `make lock` and commit `uv.lock`; npm packages are
  installed with exact versions and `package-lock.json` is committed.
- The frontend (`frontend/`, React + TypeScript, Vite) is served by the API at `/` when
  `SCAT_WEB_DIR` names a build. `frontend/node_modules` is a dev container volume (a Windows bind
  mount cannot hold npm's symlinks). It must stay database-free and same-origin (CSP).

## Commands

- `make check` (lint + typecheck + test + `make web-check`) must pass before a change is done.
- `make fmt` formats and auto-fixes; `make test` and `make typecheck` run one stage alone.
- `make run` starts the API with reload on port 8000; `make smoke` exercises a running server.
- `make web-dev` runs the frontend on port 5173 (proxying `/api` to port 8000); `make serve` serves
  the built frontend from the API. jsdom cannot measure layout: check page styles in a browser.
- `make migrate` applies migrations to the dev database. Tests need the `db` service: they create,
  migrate, and drop a throwaway database and never touch the dev data.

## Rules

- Never rewrite files under `tests/fixtures/raw/`: they are byte-exact fixtures (BOMs, CRLF,
  malformed UTF-8, exact numeric tokens). Create new fixtures with explicit bytes, not an editor.
- Files are LF-only (`.gitattributes`). Fixture JSON is UTF-8 without a BOM.
- Never log request bodies, values, query values, or exception messages (see README.md, Logging).
- `src/scat_validator/config.py` is the single source for limits, labels, and check names. The
  frontend's labels are generated from it (`make labels`); a test fails when they drift.
- The specs are `docs/scat-validation-endpoint-spec.md` (stage 1, validation),
  `docs/scat-stage-2-submission-spec.md` (stage 2, storage/export/delete, which extends stage 1),
  `docs/scat-stage-3-annotation-frontend-spec.md` (stage 3, frontend, listing, annotation
  updates), and `docs/scat-stage-4-llm-annotation-spec.md` (stage 4, annotation sets by source,
  LLM annotation; it replaces the record's `annotations` with `annotation_sets`).
  Choices they leave open are recorded in `docs/implementation-decisions.md`, and acceptance
  criteria map to tests in `docs/acceptance-mapping.md`. Update those when behavior changes.
- Never log `case_id` or `username` values, SQL parameters, or database exception messages; the
  frontend never writes them, narratives, or annotations to the console.
- Annotation updates splice the map into the stored record text; never re-serialize stored
  records (exact bytes and number tokens must survive).
