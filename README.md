# SCAT validator (tool-v3)

A prototype of the Safety Concepts Annotation Tool: a small FastAPI service for fictional case
records, with a browser frontend for annotating them, used to narrow requirements for a formal
application.

- **Stage 1** validates records, explains what it understood, and, on request, echoes the exact
  decoded input. Contract: [docs/scat-validation-endpoint-spec.md](docs/scat-validation-endpoint-spec.md)
  (revision 0.4). A successful validation means **valid under that contract**: nothing is
  stored, authenticated, authorized, or checked for clinical correctness.
- **Stage 2** stores one validated record per `(case_id, username)` in PostgreSQL, exports it
  byte for byte, and deletes it. Contract:
  [docs/scat-stage-2-submission-spec.md](docs/scat-stage-2-submission-spec.md) (revision 0.1).
- **Stage 3** adds a React + TypeScript annotation frontend (`frontend/`), served by the API at
  `/`. It lists a username's stored cases, shows each narrative in pages sized to the screen, and
  saves annotations made by selecting text and choosing a label. Saving replaces the stored
  record's annotations, guarded by a version number. Contract:
  [docs/scat-stage-3-annotation-frontend-spec.md](docs/scat-stage-3-annotation-frontend-spec.md)
  (revision 0.1). Case data still enters only through `POST /api/v1/submissions`.

> **No authentication.** `username` is an unverified claim: anyone who can reach the service can
> submit, list, export, annotate, or delete any submission. Use fictional data only; the service is published on
> loopback only.

| Endpoint | Purpose |
| --- | --- |
| `POST /api/v1/validate` | Validate one record object |
| `POST /api/v1/validate-batch` | Validate `{"records": [...]}` with 1–100 entries |
| `POST /api/v1/submissions` | Validate one record and store it (201; 409 if that `(case_id, username)` exists) |
| `GET /api/v1/submissions?username=…` | List one username's submissions (case IDs, timestamps, versions) |
| `GET /api/v1/submissions/{case_id}?username=…` | Export one stored record, wrapped with `submitted_at`, `updated_at`, `version`, and `offset_unit`; `ETag` names the version |
| `PUT /api/v1/submissions/{case_id}/annotation-sets/user?username=…` | Replace the record's user annotation set (`If-Match` required; 412 if the version changed) |
| `DELETE /api/v1/submissions/{case_id}?username=…` | Permanently delete one stored record (204) |
| `GET /healthz` | Liveness: exactly `{"status":"ok"}` (no database check) |
| `GET /docs`, `GET /openapi.json` | Interactive documentation and the OpenAPI contract |
| `GET /` | The annotation frontend (when built; always in the Compose image) |

Both validation endpoints accept the optional query `echo=true` (also `1`, `on`, `yes`, `t`, `y`, any case;
false spellings `false`, `0`, `off`, `no`, `f`, `n`). No other query parameter is accepted.

### Minimal submission

A record needs only three fields. A narrative without annotations is the default submission:

```json
{
  "case_id": "12345678-1",
  "username": "demo_annotator",
  "narrative": "Aspirin caused nausea."
}
```

- `case_id` is a string `<case number>-<version>` (ASCII, at most 64 characters), unique within a
  batch and kept exactly as sent. Numbers such as `5001` are rejected, not converted.
- `annotations` is optional: an object mapping label names to `{text, start, end}` spans (see
  `examples/annotated-record.json`). Absent means zero annotations.
- `offset_unit` is optional. Offsets are always Unicode code points. If you send `offset_unit`,
  it must be `unicode-code-point`. Each record observation in the report includes the
  `offset_unit` the service used; the echo is never changed.
- Any other property (including a legacy `project_id`) is preserved as foreign metadata and listed
  under `foreign_fields`.

## Runtime and tooling

| Component | Pinned version | Where it is pinned |
| --- | --- | --- |
| CPython | 3.14.8 (`python:3.14.8-slim-trixie`) | `Dockerfile`, `requires-python` in `pyproject.toml` |
| uv | 0.13.1 | `Dockerfile` |
| Node.js | 24.22.1 LTS (`node:24.22.1-trixie-slim`), npm 11 | `Dockerfile`, `engines` in `frontend/package.json` |
| PostgreSQL | 18.6 server (`postgres:18.6-trixie`); `psql` 18 client from the PostgreSQL apt repository (dev container only) | `compose.yaml`, `POSTGRES_CLIENT_MAJOR` in `Dockerfile` |
| Claude Code | `stable` channel at build time, auto-updater off (dev container only) | `CLAUDE_CODE_VERSION` in `Dockerfile` |
| Dependencies | exact versions (FastAPI, Pydantic v2, uvicorn, email-validator, SQLAlchemy 2.1, psycopg 3, Alembic; ruff, mypy, pytest, httpx) | `pyproject.toml` + `uv.lock` |
| Frontend dependencies | exact versions (React 19, Vite 8, TypeScript 6, Vitest 5, ESLint 10, self-hosted fonts) | `frontend/package.json` + `frontend/package-lock.json` |

You don't need Python, uv, Node, or PostgreSQL on the host. Docker and VS Code are enough.

## Prerequisites (Windows host)

1. Docker Desktop running with the WSL 2 backend (Linux containers). With the Docker VMM
   backend instead, add the folder that holds your projects (for example
   `C:\Users\<you>\Workspaces`) under **Settings → Resources → File Sharing**, then
   **Apply & restart**. Share your code root rather than your whole user profile, so containers
   can't mount `.ssh` or other credentials. The WSL 2 backend needs no File Sharing entry.
2. VS Code with the **Dev Containers** extension (`ms-vscode-remote.remote-containers`).

## Option A: Develop in the dev container

1. In VS Code: **File → Open Folder…** and select `tool-v3`.
2. Command Palette (`Ctrl+Shift+P`) → **Dev Containers: Reopen in Container**.
   The first build takes a few minutes; `postCreateCommand` then runs `uv sync --locked`,
   installs the frontend packages (`make web-install`), and applies the database migrations
   (`make migrate`).
3. Open a terminal in VS Code (it opens inside the container at `/app`) and use:

| Command | Purpose |
| --- | --- |
| `make fmt` | Format and auto-fix lint issues (ruff) |
| `make lint` | Check formatting and lint (ruff) |
| `make typecheck` | Strict type checking (mypy) |
| `make test` | Run all tests (pytest; needs the `db` service, see [Database](#database)) |
| `make check` | lint + typecheck + test, then the frontend checks (`make web-check`) |
| `make run` | Start the API with auto-reload on `0.0.0.0:8000` |
| `make smoke` | Smoke-test a running server (default `http://127.0.0.1:8000`; override with `BASE_URL=...`) |
| `make lock` | Re-resolve `uv.lock` after editing dependencies in `pyproject.toml` |
| `make psql` | Open `psql` on the dev container's database |
| `make migrate` | Apply database migrations (Alembic) to the dev container's database |
| `make db-wait` | Wait up to 30 s for the database to accept connections |
| `make web-dev` | Frontend dev server on port 5173 with hot reload, proxying `/api` to `make run` |
| `make web-test`, `make web-lint` | Frontend tests (Vitest), and ESLint + `tsc` |
| `make web-build` | Build the frontend into `frontend/dist` |
| `make serve` | Build the frontend and serve it from the API on port 8000, as Compose does |
| `make labels` | Regenerate `frontend/src/generated/labels.ts` after changing `LABELS` in `config.py` |
| `make web-install` | Reinstall the locked frontend packages (`npm ci`) |

To smoke-test the real server: run `make run` in one container terminal and `make smoke` in a
second one. While `make run` is active, VS Code forwards port 8000 to the host (usually
`http://localhost:8000`; check the **Ports** panel if 8000 was already taken).

The dev container also includes `gh`, `ripgrep`, and Claude Code. Run `claude` in a container
terminal and log in once: login and settings live in the `claude-config` volume, so they survive
rebuilds. Project guidance for Claude is in `CLAUDE.md`, and shared permissions are in
`.claude/settings.json` (personal overrides go in the git-ignored `.claude/settings.local.json`).

`frontend/node_modules` lives in the `frontend-node-modules` volume rather than in the bind
mount: npm needs symlinks there, which a Windows folder cannot hold, and the volume is faster.

After changing `Dockerfile`, `compose.yaml`, `.devcontainer/`, or `.env`, run
**Dev Containers: Rebuild Container**.

## Option B: Run the service with Docker Compose

From a host terminal (PowerShell) in the `tool-v3` directory:

```powershell
docker compose up --build
```

The service listens on `0.0.0.0:8000` inside the container and is published only on
`127.0.0.1:8000` on the host.

- Annotation frontend: <http://localhost:8000/>
- Health: <http://localhost:8000/healthz> → `{"status":"ok"}`
- Interactive API docs: <http://localhost:8000/docs>
- OpenAPI: <http://localhost:8000/openapi.json>

Stop the service with `Ctrl+C` in that terminal, then run `docker compose down`.

`docker compose up` also starts the `db` service; `api` starts once the database is healthy, applies
the migrations, and then serves.

## Database

PostgreSQL 18.6 runs as the `db` Compose service, for both the dev container and
`docker compose up`. Only the submission routes use it; validation and `/healthz` work without it.
When the database is unreachable (or `DATABASE_URL` is unset), the submission routes answer
`503 STORAGE_UNAVAILABLE`.

- **Schema**: one `submissions` table (`case_id`, `username`, the exact current record text, the
  record as first submitted, `submitted_at`, `updated_at`, and `version`, unique on
  `(case_id, username)`), created by Alembic migrations in
  `src/scat_validator/migrations/`. Compose applies them when `api` starts; in the dev container
  they run on create, and `make migrate` applies new ones. A new migration:
  `uv run --locked alembic revision -m "<message>"`, then mirror it in `storage.py`
  (`make test` runs `alembic check`).
- **Tests** create a throwaway database (`scat_test_<random>`) on the same server, migrate it,
  and drop it afterwards, so they never touch your dev data. They fail if the database is
  unreachable.
- **Credentials** are dev-only defaults (database, user, and password all `scat`). To change
  them, copy `.env.example` to `.env` (git-ignored) before the first start. The values only take
  effect when a volume is created, so reset the volume after changing them.
- **Inside the dev container**, `PGHOST`, `PGUSER`, `PGDATABASE`, and `PGPASSWORD` are set, so
  `psql` (or `make psql`) connects with no arguments. Code should read `DATABASE_URL`:
  `postgresql://scat:scat@db:5432/scat`.
- **From Windows tools** (DBeaver, pgAdmin): while the dev container is open, VS Code forwards
  `db:5432`. Use the local address shown in the **Ports** panel (usually `localhost:5432`, or
  another port if 5432 is taken). The database port is never published by Compose, so
  `docker compose up` alone gives no host access.
- **Data** lives in a named volume per Compose project and survives restarts and rebuilds:
  `scat-v3-devcontainer_pgdata` for the dev container and `scat-v3_pgdata` for
  `docker compose up`. The two databases are independent.
- **Reset** (deletes all data) from a host terminal in `tool-v3`: close the dev container window,
  then run `docker compose -p scat-v3-devcontainer down -v` for the dev container database, or
  `docker compose down -v` for the `docker compose up` one.

## Exercising the API

Run these from a **second terminal in the `tool-v3` directory**, since the fixture paths are
relative. In Windows PowerShell type `curl.exe`, not `curl`, which is an alias for
`Invoke-WebRequest`. In bash, inside the dev container or on Linux/macOS, `curl` works as shown.
`--include` prints the status line and headers before the JSON body. `--data-binary @file` sends
the fixture bytes unchanged, with no shell interpolation of JSON.

### Validation

```bash
# 1. Minimal record -> HTTP 200 and a validation report
curl --include --header 'Content-Type: application/json' --data-binary '@examples/valid-record.json' http://localhost:8000/api/v1/validate

# 2. Same record with echo -> HTTP 200; the report also contains "echo" with the exact input
curl --include --header 'Content-Type: application/json' --data-binary '@examples/valid-record.json' 'http://localhost:8000/api/v1/validate?echo=true'

# 3. Batch with one wrong quote -> HTTP 422, one TEXT_MISMATCH at /records/1/annotations/AE/0/text
curl --include --header 'Content-Type: application/json' --data-binary '@examples/invalid-batch.json' http://localhost:8000/api/v1/validate-batch
```

The PowerShell form is the same with `curl.exe` and double quotes:
`curl.exe --include --header "Content-Type: application/json" --data-binary "@examples/valid-record.json" "http://localhost:8000/api/v1/validate?echo=true"`.

To diagnose a failure, read `error.code`, then each entry of `issues` (`path` is a JSON Pointer
into your body, and `code` is a stable rule identifier), and the per-record `checks`. Correct the
input and resend it. `examples/` holds more fixtures: a record with pre-existing annotations
(`annotated-record.json`), foreign metadata (`metadata-record.json`), exact numeric metadata,
Unicode/CRLF offsets, overlapping spans, username boundaries, reserved-looking metadata names, a
long diagnostic path, and repeated case identifiers.

Fixture files must be saved as **UTF-8 without a BOM**. The server tolerates one leading BOM,
but senders should not add one.

### Submissions

Run `make migrate` once first (the dev container does it on create). In bash:

```bash
# Store the minimal record -> 201, a receipt, and a Location header with the export URL
curl --include --header 'Content-Type: application/json' --data-binary '@examples/valid-record.json' http://localhost:8000/api/v1/submissions

# Same pair again -> 409 ALREADY_SUBMITTED (delete it first to replace it)
curl --include --header 'Content-Type: application/json' --data-binary '@examples/valid-record.json' http://localhost:8000/api/v1/submissions

# Export -> 200 {"case_id", "username", "submitted_at", "offset_unit", "record": <exact record>}
curl --include --get --data-urlencode 'username=demo_annotator' http://localhost:8000/api/v1/submissions/12345678-1

# Delete -> 204; exporting again -> 404 NOT_FOUND
curl --include --request DELETE --get --data-urlencode 'username=demo_annotator' http://localhost:8000/api/v1/submissions/12345678-1
```

`username` goes in the query string, not the path, because valid usernames may contain `/`, `?`,
`#`, and `%`. `--get --data-urlencode` percent-encodes it (including `+` as `%2B`; an unencoded
`+` decodes to a space and is rejected). Both values must match exactly: `Reviewer@Example.com`
and `reviewer@example.com` are different users. Submissions accept no query parameters (`echo`
included); the export returns the whole record exactly as validated, with foreign metadata and
exact numeric tokens.

### Annotating in the browser

1. Store a case through the API (for example the first command under [Submissions](#submissions)).
2. Open the frontend: <http://localhost:8000/> with `docker compose up` or `make serve`; or run
   `make run` and `make web-dev` together in the dev container and open port 5173.
3. Enter the username the case was submitted under (`demo_annotator` for the examples) and open
   the case.
4. Select text with the mouse and choose a label (type to filter, Enter to pick). To annotate
   across a page break, select the start, go to the next page, and Shift+click where it ends.
   Click a highlight to change its label or delete it. Overlapping annotations each get their
   own stripe under the text; the list on the right shows every annotation by label.
5. **Save** (or Ctrl+S) stores the annotations; export the case to see them in the record. If the
   case was saved elsewhere meanwhile, the app asks you to reload it.

Pages follow the size of the reading area, so resizing the window or zooming moves the page
breaks; offsets never change. jsdom tests cannot measure real layout, so after changing page
styles, check pagination, stripes, and the popups in a browser.

## LLM annotation

The frontend can ask a language model to propose annotations for a case. A proposal is stored
by the server, shown read-only, and becomes an `llm` annotation set only when you accept it. The
feature is off until a provider is configured; without one, everything else works as before.

| Variable | Meaning |
| --- | --- |
| `SCAT_LLM_PROVIDER` | `anthropic` (Messages API) or `openai` (any OpenAI-compatible Chat Completions server). |
| `SCAT_LLM_MODEL` | Model name; must match the model pattern of the stage 4 spec (§2.2). |
| `SCAT_LLM_API_KEY` | Secret. Required for `anthropic`; optional for `openai` (local servers). |
| `SCAT_LLM_BASE_URL` | Optional. Defaults: `https://api.anthropic.com` and `https://api.openai.com/v1`. Lets `openai` reach Ollama, vLLM, or Azure-style gateways. |
| `SCAT_LLM_TIMEOUT_SECONDS` | Optional; default 600. Models that reason before answering can take minutes on a long narrative. |
| `SCAT_LLM_MAX_OUTPUT_TOKENS` | Optional; default 32,000. |

Compose passes these through from the host environment or a git-ignored `.env` (see
`.env.example`); an empty value counts as unset. In the dev container, export them before
`make run`.

**Data egress.** With a cloud provider, case narratives leave this service and are sent to that
provider. Use a local server (below) when they must not.

Anthropic:

```
SCAT_LLM_PROVIDER=anthropic
SCAT_LLM_MODEL=claude-opus-5-5
SCAT_LLM_API_KEY=<your key>
```

Ollama on the Windows host, reached from the container (no key):

```
SCAT_LLM_PROVIDER=openai
SCAT_LLM_BASE_URL=http://host.docker.internal:11434/v1
SCAT_LLM_MODEL=llama3.1:8b
```

Verify the configuration (secrets are never shown):

```bash
curl -s http://localhost:8000/api/v1/llm
```

The model must return the narrative unchanged with only tags added; if its output does not
reproduce the narrative exactly, the run fails rather than guessing, and running it again may
help.

## Project layout

| Path | Responsibility |
| --- | --- |
| `src/scat_validator/config.py` | Limits, ordered labels, check names (single source for validation and docs) |
| `src/scat_validator/wire.py` | Bounded body reading, strict UTF-8/BOM, depth guard, JSON decoding with exact number tokens, duplicate names |
| `src/scat_validator/http_gates.py` | Content-Type, Content-Encoding, declared length, and query-option gates |
| `src/scat_validator/validation.py` | Pure record validator: the nine checks, observations, and inventory |
| `src/scat_validator/paths.py`, `diagnostics.py` | Bounded JSON Pointers with whole-token fallback, capped collectors, fixed messages |
| `src/scat_validator/reporting.py` | Batch orchestration, envelope rules, report assembly, budgeted serialization and echo |
| `src/scat_validator/contracts.py`, `openapi_docs.py` | Pydantic schemas, shared strict field types, explicit OpenAPI |
| `src/scat_validator/api.py` | Thin routes, gate order, status codes, `Cache-Control: no-store`, logging, storage lifespan |
| `src/scat_validator/submissions.py` | Submission identity parameters (`case_id` path, `username` query) and receipt/export/failure bodies |
| `src/scat_validator/updates.py` | Annotation-update body shape, byte-exact splicing of the map into the stored record, entity tags |
| `src/scat_validator/storage.py`, `migrations/` | PostgreSQL `submissions` table (SQLAlchemy Core, psycopg 3) and its Alembic migrations |
| `frontend/src/lib/` | Frontend logic without React: code-point offsets, annotations and their exact serialization, pagination, segments, API client |
| `frontend/src/components/`, `hooks/` | The annotator (page, label picker, inspector, panel), case list, and the measuring pagination hook |
| `frontend/src/generated/labels.ts` | Label keys generated from `config.py` (`make labels`) |
| `tests/` | Raw-byte ASGI tests, pure unit tests, raw byte fixtures in `tests/fixtures/raw/`, and database fixtures (`conftest.py`) |
| `scripts/smoke_test.py` | Real-server smoke test (standard library only) |
| `docs/acceptance-mapping.md` | V01–V22, S01–S10, and F01–F14 mapped to tests and commands |
| `docs/implementation-decisions.md` | Implementation choices the spec leaves open |

## Fixtures and line endings

`.gitattributes` forces LF line endings so files work inside the Linux container regardless of
the host's `core.autocrlf`. It also treats `tests/fixtures/raw/**` as binary so that byte-exact
fixtures (BOMs, malformed UTF-8, exact numeric tokens, duplicate names) are never rewritten.

## Logging

The frontend never writes narratives, annotations, or usernames to the browser console (ESLint
forbids `console`), and the app does not log requests for its static files.

The app logs one line per request to its API routes: method, route template (for example
`GET /api/v1/submissions/{case_id}`), status, duration, record count, and issue count. Uvicorn's
access log is disabled (`--no-access-log`) because it would record query values. Bodies, values
(including `case_id` and `username`), SQL parameters, and exception messages are never logged. An
unexpected fault or unreachable database logs only its exception type and source location.

Each LLM call also logs one line: provider, model, status, duration, output length, annotation
count, and reason. It describes the provider call, not the final HTTP response, and nothing else
from the call (prompt, narrative, output, URL, key) is logged. The `httpx` and `httpcore` loggers
are set to WARNING because httpx logs request URLs at INFO.

## Troubleshooting

- **Port 8000 already in use** (`bind: address already in use` or `ports are not available`):
  find the owner with `netstat -ano | findstr :8000` in PowerShell, then stop it or the leftover
  container (`docker ps`, then `docker compose down`). The dev container does not publish 8000
  itself, so it can run alongside `docker compose up`, but `make run` inside it and
  `docker compose up` both want host port 8000 through forwarding. Run one at a time, or use the
  forwarded port VS Code shows.
- **Container fails to start**: run `docker compose logs api` and `docker compose ps` (the
  health check polls `/healthz`). To rebuild from scratch, run `docker compose build --no-cache`.
- **Dev container fails to build**: Command Palette → **Dev Containers: Show Container Log**.
- **`api` never starts and `db` is unhealthy**: run `docker compose logs db` (add
  `-p scat-v3-devcontainer` for the dev container). `password authentication failed` after
  editing `.env` means the volume still has the old credentials: reset it (see
  [Database](#database)).
- **`perl: warning: Setting locale failed`** from `psql` or `pg_isready`: `LANG` names a locale
  the image lacks (VS Code sets it from your display language). The dev container sets
  `LANG=C.UTF-8` and turns off `terminal.integrated.detectLocale`; rebuild it, or run
  `export LANG=C.UTF-8` in an older terminal.
- **`psql: could not translate host name "db"`**: the `db` service is not running. Rebuild the dev
  container, or run `make db-wait` if it is still starting.
- **`the path "C:\..." is not shared from the host`**: Docker Desktop is using a backend that
  only mounts listed folders (such as Docker VMM). Add the parent folder of `tool-v3` under
  **Settings → Resources → File Sharing** (see Prerequisites), apply, and rebuild the container.
- **TLS/certificate errors while downloading packages** (common behind corporate proxies):
  the build needs your organization's root CA certificate added to the image.
- **`uv sync --locked` fails with "lockfile needs to be updated"**: `pyproject.toml` changed
  without re-locking. Run `make lock` in the dev container and commit `uv.lock`.
- **`503 STORAGE_UNAVAILABLE` from the submission routes**: the database is unreachable or not
  migrated. Run `make db-wait`, then `make migrate`; for Compose, check `docker compose logs api`
  for the migration output.
- **Tests fail with "PostgreSQL is not reachable"**: the `db` service is down. Rebuild the dev
  container, or run `make db-wait` if it is still starting.
- **`415 UNSUPPORTED_MEDIA_TYPE` from curl**: add `--header 'Content-Type: application/json'`.
  curl's `--data-binary` default is `application/x-www-form-urlencoded`.
