# SCAT validator (tool-v3)

Stage 1 of the Safety Concepts Annotation Tool: a small FastAPI service that validates fictional
case records, explains what it understood, and, on request, echoes the exact decoded input. The
implementation contract is [docs/scat-validation-endpoint-spec.md](docs/scat-validation-endpoint-spec.md)
(revision 0.4).

A successful response means **valid under that contract**. Nothing is stored, authenticated,
authorized, looked up in a database, or checked for clinical correctness. There is no frontend.
Compose already runs a PostgreSQL container for the next (stateful) stage, but the validator never
connects to it (see [Database](#database)).

| Endpoint | Purpose |
| --- | --- |
| `POST /api/v1/validate` | Validate one record object |
| `POST /api/v1/validate-batch` | Validate `{"records": [...]}` with 1–100 entries |
| `GET /healthz` | Liveness: exactly `{"status":"ok"}` |
| `GET /docs`, `GET /openapi.json` | Interactive documentation and the OpenAPI contract |

Both POST endpoints accept the optional query `echo=true` (also `1`, `on`, `yes`, `t`, `y`, any case;
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
| PostgreSQL | 18.6 server (`postgres:18.6-trixie`); `psql` 18 client from the PostgreSQL apt repository (dev container only) | `compose.yaml`, `POSTGRES_CLIENT_MAJOR` in `Dockerfile` |
| Claude Code | `stable` channel at build time, auto-updater off (dev container only) | `CLAUDE_CODE_VERSION` in `Dockerfile` |
| Dependencies | exact versions (FastAPI, Pydantic v2, uvicorn, email-validator; ruff, mypy, pytest, httpx) | `pyproject.toml` + `uv.lock` |

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
   The first build takes a few minutes; `postCreateCommand` then runs `uv sync --locked`.
3. Open a terminal in VS Code (it opens inside the container at `/app`) and use:

| Command | Purpose |
| --- | --- |
| `make fmt` | Format and auto-fix lint issues (ruff) |
| `make lint` | Check formatting and lint (ruff) |
| `make typecheck` | Strict type checking (mypy) |
| `make test` | Run all tests (pytest) |
| `make check` | lint + typecheck + test |
| `make run` | Start the API with auto-reload on `0.0.0.0:8000` |
| `make smoke` | Smoke-test a running server (default `http://127.0.0.1:8000`; override with `BASE_URL=...`) |
| `make lock` | Re-resolve `uv.lock` after editing dependencies in `pyproject.toml` |
| `make psql` | Open `psql` on the dev container's database |
| `make db-wait` | Wait up to 30 s for the database to accept connections |

To smoke-test the real server: run `make run` in one container terminal and `make smoke` in a
second one. While `make run` is active, VS Code forwards port 8000 to the host (usually
`http://localhost:8000`; check the **Ports** panel if 8000 was already taken).

The dev container also includes `gh`, `ripgrep`, and Claude Code. Run `claude` in a container
terminal and log in once: login and settings live in the `claude-config` volume, so they survive
rebuilds. Project guidance for Claude is in `CLAUDE.md`, and shared permissions are in
`.claude/settings.json` (personal overrides go in the git-ignored `.claude/settings.local.json`).

After changing `Dockerfile`, `compose.yaml`, `.devcontainer/`, or `.env`, run
**Dev Containers: Rebuild Container**.

## Option B: Run the service with Docker Compose

From a host terminal (PowerShell) in the `tool-v3` directory:

```powershell
docker compose up --build
```

The service listens on `0.0.0.0:8000` inside the container and is published only on
`127.0.0.1:8000` on the host.

- Health: <http://localhost:8000/healthz> → `{"status":"ok"}`
- Interactive API docs: <http://localhost:8000/docs>
- OpenAPI: <http://localhost:8000/openapi.json>

Stop the service with `Ctrl+C` in that terminal, then run `docker compose down`.

`docker compose up` also starts the `db` service; `api` starts once the database is healthy.

## Database

PostgreSQL 18.6 runs as the `db` Compose service, for both the dev container and
`docker compose up`. Stage 1 does not use it: `DATABASE_URL` is set on `api` but no code reads it.

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
| `src/scat_validator/api.py` | Thin routes, gate order, status codes, `Cache-Control: no-store`, logging |
| `tests/` | Raw-byte ASGI tests, pure unit tests, and raw byte fixtures in `tests/fixtures/raw/` |
| `scripts/smoke_test.py` | Real-server smoke test (standard library only) |
| `docs/acceptance-mapping.md` | V01–V22 mapped to tests and commands |
| `docs/implementation-decisions.md` | Implementation choices the spec leaves open |

## Fixtures and line endings

`.gitattributes` forces LF line endings so files work inside the Linux container regardless of
the host's `core.autocrlf`. It also treats `tests/fixtures/raw/**` as binary so that byte-exact
fixtures (BOMs, malformed UTF-8, exact numeric tokens, duplicate names) are never rewritten.

## Logging

The app logs one line per validation request: route, status, duration, record count, and issue
count. Uvicorn's access log is disabled (`--no-access-log`) because it would record query values.
Bodies, values, and exception messages are never logged. An unexpected fault logs only its
exception type and source location.

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
- **`psql: could not translate host name "db"`**: the `db` service is not running. Rebuild the dev
  container, or run `make db-wait` if it is still starting.
- **`the path "C:\..." is not shared from the host`**: Docker Desktop is using a backend that
  only mounts listed folders (such as Docker VMM). Add the parent folder of `tool-v3` under
  **Settings → Resources → File Sharing** (see Prerequisites), apply, and rebuild the container.
- **TLS/certificate errors while downloading packages** (common behind corporate proxies):
  the build needs your organization's root CA certificate added to the image.
- **`uv sync --locked` fails with "lockfile needs to be updated"**: `pyproject.toml` changed
  without re-locking. Run `make lock` in the dev container and commit `uv.lock`.
- **`415 UNSUPPORTED_MEDIA_TYPE` from curl**: add `--header 'Content-Type: application/json'`.
  curl's `--data-binary` default is `application/x-www-form-urlencoded`.
