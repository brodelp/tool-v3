# syntax=docker/dockerfile:1

# Pinned runtimes. Keep PYTHON_IMAGE consistent with requires-python in pyproject.toml.
ARG PYTHON_IMAGE=python:3.14.8-slim-trixie
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.1

FROM ${UV_IMAGE} AS uv

# ---------------------------------------------------------------- base
FROM ${PYTHON_IMAGE} AS base
COPY --from=uv /uv /uvx /usr/local/bin/
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"
WORKDIR /app

# ---------------------------------------------------------------- dev
# Dev container target: tooling + non-root user; source is bind-mounted at /app.
FROM base AS dev
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
# gh comes from GitHub's apt repository and the PostgreSQL client from the PostgreSQL (PGDG) apt
# repository; everything else from Debian. Debian trixie ships client 17, whose pg_dump refuses an
# 18 server. Keep POSTGRES_CLIENT_MAJOR in step with the db image in compose.yaml.
ARG POSTGRES_CLIENT_MAJOR=18
RUN apt-get update \
    && apt-get install -y --no-install-recommends git make curl ca-certificates ripgrep \
    && install -d -m 0755 /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /etc/apt/keyrings/pgdg.asc \
    && chmod go+r /etc/apt/keyrings/pgdg.asc \
    && . /etc/os-release \
    && echo "deb [signed-by=/etc/apt/keyrings/pgdg.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh "postgresql-client-${POSTGRES_CLIENT_MAJOR}" \
    && rm -rf /var/lib/apt/lists/*
ARG USERNAME=vscode
ARG USER_UID=1000
ARG USER_GID=1000
RUN groupadd --gid "${USER_GID}" "${USERNAME}" \
    && useradd --uid "${USER_UID}" --gid "${USER_GID}" --create-home --shell /bin/bash "${USERNAME}" \
    && mkdir -p /opt/venv \
    && chown "${USERNAME}:${USERNAME}" /opt/venv /app
USER ${USERNAME}
# Claude Code (native installer). Config lives in ~/.claude, which the dev container mounts as a
# named volume; creating it here makes the fresh volume owned by the user, not root.
# The auto-updater is off so the version only changes on rebuild.
ARG CLAUDE_CODE_VERSION=stable
ENV PATH="/home/${USERNAME}/.local/bin:${PATH}" \
    CLAUDE_CONFIG_DIR=/home/${USERNAME}/.claude \
    DISABLE_AUTOUPDATER=1
RUN mkdir -p "/home/${USERNAME}/.claude" \
    && curl -fsSL https://claude.ai/install.sh | bash -s "${CLAUDE_CODE_VERSION}"
# Pre-install locked dependencies (incl. dev group) so the container opens ready;
# postCreateCommand then installs the project itself in editable mode.
COPY --chown=${USERNAME}:${USERNAME} pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/home/vscode/.cache/uv,uid=1000,gid=1000 \
    uv sync --locked --no-install-project

# ---------------------------------------------------------------- runtime
# Compose target: production dependencies only, non-root, no source mount.
FROM base AS runtime
ENV UV_COMPILE_BYTECODE=1
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable
RUN groupadd --system app && useradd --system --gid app --no-create-home app
USER app
EXPOSE 8000
# No uvicorn access log: it would record query values. The app logs route/status/duration/counts.
CMD ["uvicorn", "scat_validator.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
