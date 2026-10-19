# SCAT validator (tool-v3)

Stage 1 of the Safety Concepts Annotation Tool: a FastAPI service that validates and inspects
fictional case data. The implementation contract is
[docs/scat-validation-endpoint-spec.md](docs/scat-validation-endpoint-spec.md).

> Status: development environment only. `GET /healthz` is implemented; the validation endpoints
> are not yet.

## Runtime and tooling

| Component | Pinned version | Where it is pinned |
| --- | --- | --- |
| CPython | 3.14.8 (`python:3.14.8-slim-trixie`) | `Dockerfile`, `requires-python` in `pyproject.toml` |
| uv | 0.13.1 | `Dockerfile` |
| Dependencies | exact versions | `pyproject.toml` + `uv.lock` |

You don't need Python, uv, Node, or PostgreSQL on the host — only Docker and VS Code.

## Prerequisites (Windows host)

1. Docker Desktop running with the WSL 2 backend (Linux containers).
2. VS Code with the **Dev Containers** extension (`ms-vscode-remote.remote-containers`).

## Option A — Develop in the dev container

1. In VS Code: **File → Open Folder…** and select `tool-v3`.
2. Command Palette (`Ctrl+Shift+P`) → **Dev Containers: Reopen in Container**.
   The first build takes a few minutes; `postCreateCommand` then runs `uv sync --locked`.
3. Open a terminal in VS Code (it opens inside the container at `/app`) and use:

| Command | Purpose |
| --- | --- |
| `make fmt` | Format and auto-fix lint issues (ruff) |
| `make lint` | Check formatting and lint (ruff) |
| `make typecheck` | Strict type checking (mypy) |
| `make test` | Run tests (pytest) |
| `make check` | lint + typecheck + test |
| `make run` | Start the API with auto-reload on `0.0.0.0:8000` |
| `make lock` | Re-resolve `uv.lock` after editing dependencies in `pyproject.toml` |

While `make run` is active, VS Code forwards port 8000 to the host
(usually `http://localhost:8000`; check the **Ports** panel if 8000 was already taken).

After changing `Dockerfile`, `compose.yaml`, or `.devcontainer/`, run
**Dev Containers: Rebuild Container**.

## Option B — Run the service with Docker Compose

From a host terminal (PowerShell) in the `tool-v3` directory:

```powershell
docker compose up --build
```

The service listens on `0.0.0.0:8000` inside the container and is published only on
`127.0.0.1:8000` on the host.

- Health: <http://localhost:8000/healthz> → `{"status":"ok"}`
- API docs: <http://localhost:8000/docs>
- OpenAPI: <http://localhost:8000/openapi.json>

In a second PowerShell terminal, use `curl.exe` (not `curl`, which is an alias for
`Invoke-WebRequest` in Windows PowerShell). `--include` prints the status line and headers
before the body:

```powershell
curl.exe --include http://localhost:8000/healthz
```

Stop the service with `Ctrl+C` in the first terminal, then `docker compose down`.

## Fixtures and line endings

`.gitattributes` forces LF line endings so files work inside the Linux container regardless of
the host's `core.autocrlf`, and treats `tests/fixtures/raw/**` as binary so byte-exact fixtures
(BOMs, CRLF, malformed UTF-8, exact numeric tokens) are never rewritten. Save JSON fixtures as
UTF-8 **without** a BOM.

## Troubleshooting

- **Port 8000 already in use** (`bind: address already in use` or `ports are not available`):
  find the owner with `netstat -ano | findstr :8000` in PowerShell, stop it or a leftover
  container (`docker ps`, then `docker compose down`). The dev container does not publish 8000
  itself, so it can run alongside `docker compose up`.
- **Container fails to start**: run `docker compose logs api`. Rebuild from scratch with
  `docker compose build --no-cache`.
- **Dev container fails to build**: Command Palette → **Dev Containers: Show Container Log**.
- **TLS/certificate errors while downloading packages** (common behind corporate proxies):
  the build needs your organization's root CA certificate added to the image.
- **`uv sync --locked` fails with "lockfile needs to be updated"**: `pyproject.toml` changed
  without re-locking; run `make lock` in the dev container and commit `uv.lock`.
