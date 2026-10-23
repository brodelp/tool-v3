"""Database fixtures for the stage 2 submission tests.

Tests run against a real PostgreSQL server (the dev container's `db` service, via DATABASE_URL):
each session creates a throwaway database, applies the Alembic migrations to it, and drops it
at the end. Nothing touches the developer's own database. Stage 1 tests need none of this.
"""

import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from scat_validator.api import app
from scat_validator.storage import (
    DATABASE_URL_VARIABLE,
    Storage,
    create_storage_engine,
    sqlalchemy_url,
)
from tests.helpers import ROOT


def alembic_config(database_url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    config.attributes["configure_logger"] = False  # keep pytest's log capture intact
    return config


def _admin_engine(base_url: str) -> Engine:
    return create_engine(sqlalchemy_url(base_url), isolation_level="AUTOCOMMIT")


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """URL of a freshly migrated, session-private database."""
    base_url = os.environ.get(DATABASE_URL_VARIABLE)
    if not base_url:
        pytest.fail(f"{DATABASE_URL_VARIABLE} is not set; open the dev container (README.md).")
    name = f"scat_test_{uuid4().hex[:12]}"
    admin = _admin_engine(base_url)
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
    except Exception as exc:
        admin.dispose()
        pytest.fail(f"PostgreSQL is not reachable ({type(exc).__name__}); see README.md Database.")
    url = make_url(base_url).set(database=name).render_as_string(hide_password=False)
    try:
        command.upgrade(alembic_config(url), "head")
        yield url
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture(scope="session")
def _session_storage(database_url: str) -> Iterator[Storage]:
    storage = Storage(create_storage_engine(database_url))
    yield storage
    storage.dispose()


@pytest.fixture
def storage(_session_storage: Storage) -> Iterator[Storage]:
    """Empty submission storage installed in the app for one test."""
    with _session_storage.engine.begin() as connection:
        connection.execute(text("TRUNCATE submissions"))
    app.state.storage = _session_storage
    yield _session_storage
    app.state.storage = None
