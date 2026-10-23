"""Alembic environment: online migrations against DATABASE_URL (or a URL set by the caller)."""

import logging.config
import os

from alembic import context
from sqlalchemy import create_engine, pool

from scat_validator.storage import DATABASE_URL_VARIABLE, metadata, sqlalchemy_url

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    logging.config.fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = metadata


def _database_url() -> str:
    url = config.get_main_option("sqlalchemy.url") or os.environ.get(DATABASE_URL_VARIABLE)
    if not url:
        raise SystemExit(f"{DATABASE_URL_VARIABLE} is not set; nothing to migrate.")
    return sqlalchemy_url(url)


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline (--sql) migrations are not supported.")
run_migrations_online()
