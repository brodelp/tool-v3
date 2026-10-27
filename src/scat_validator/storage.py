"""PostgreSQL storage for submitted records (stage 2 spec §4, stage 3 spec §2).

Database entities live here, separate from the API contracts. Records are stored as the exact
validated JSON text; ``jsonb`` would rewrite number spellings, key order, and whitespace.
``source_json`` is the current record (what export returns); ``original_json`` is the record as
first submitted and never changes.
"""

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Engine,
    Identity,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
    func,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import make_url
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from scat_validator.config import (
    DB_CONNECT_TIMEOUT_SECONDS,
    MAX_CASE_ID_CHARS,
    MAX_USERNAME_CHARS,
)

DATABASE_URL_VARIABLE: Final = "DATABASE_URL"

metadata = MetaData()

submissions = Table(
    "submissions",
    metadata,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("case_id", Text, nullable=False),
    Column("username", Text, nullable=False),
    Column("source_json", Text, nullable=False),
    Column("submitted_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("original_json", Text, nullable=False),
    Column("version", BigInteger, nullable=False, server_default=text("1")),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("case_id", "username", name="submissions_case_id_username_key"),
    CheckConstraint(
        f"char_length(case_id) BETWEEN 1 AND {MAX_CASE_ID_CHARS}",
        name="submissions_case_id_length",
    ),
    CheckConstraint(
        f"char_length(username) BETWEEN 1 AND {MAX_USERNAME_CHARS}",
        name="submissions_username_length",
    ),
    CheckConstraint("version >= 1", name="submissions_version_positive"),
)

# Failures that mean "the database cannot be reached", answered with 503 rather than 500.
UNAVAILABLE_ERRORS: Final = (OperationalError, InterfaceError, PoolTimeoutError)


def sqlalchemy_url(database_url: str) -> str:
    """Select the psycopg 3 driver for a plain ``postgresql://`` URL."""
    url = make_url(database_url)
    if url.drivername in ("postgresql", "postgres"):
        url = url.set(drivername="postgresql+psycopg")
    return url.render_as_string(hide_password=False)


def create_storage_engine(database_url: str) -> Engine:
    return create_engine(
        sqlalchemy_url(database_url),
        pool_pre_ping=True,
        connect_args={"connect_timeout": DB_CONNECT_TIMEOUT_SECONDS},
    )


@dataclass(frozen=True, slots=True)
class StoredSubmission:
    case_id: str
    username: str
    source_json: str
    submitted_at: datetime
    updated_at: datetime
    version: int


@dataclass(frozen=True, slots=True)
class SubmissionVersion:
    """Timestamps and version of one stored submission, without its record."""

    case_id: str
    submitted_at: datetime
    updated_at: datetime
    version: int


class Storage:
    """Submission repository. Every method is synchronous; callers run it in a worker thread."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @classmethod
    def from_environment(cls) -> Storage | None:
        database_url = os.environ.get(DATABASE_URL_VARIABLE)
        return cls(create_storage_engine(database_url)) if database_url else None

    def insert(self, case_id: str, username: str, source_json: str) -> datetime | None:
        """Store a new submission; ``None`` when ``(case_id, username)`` already exists."""
        statement = (
            insert(submissions)
            .values(
                case_id=case_id,
                username=username,
                source_json=source_json,
                original_json=source_json,
            )
            .on_conflict_do_nothing(index_elements=["case_id", "username"])
            .returning(submissions.c.submitted_at)
        )
        with self.engine.begin() as connection:
            submitted_at: datetime | None = connection.execute(statement).scalar_one_or_none()
        return submitted_at

    def fetch(self, case_id: str, username: str) -> StoredSubmission | None:
        statement = select(
            submissions.c.case_id,
            submissions.c.username,
            submissions.c.source_json,
            submissions.c.submitted_at,
            submissions.c.updated_at,
            submissions.c.version,
        ).where(submissions.c.case_id == case_id, submissions.c.username == username)
        with self.engine.connect() as connection:
            row = connection.execute(statement).one_or_none()
        return None if row is None else StoredSubmission(*row)

    def list_for(self, username: str) -> list[SubmissionVersion]:
        """All submissions of one username, by ``case_id`` in code-point (byte) order."""
        statement = (
            select(
                submissions.c.case_id,
                submissions.c.submitted_at,
                submissions.c.updated_at,
                submissions.c.version,
            )
            .where(submissions.c.username == username)
            .order_by(submissions.c.case_id.collate("C"))
        )
        with self.engine.connect() as connection:
            rows = connection.execute(statement).all()
        return [SubmissionVersion(*row) for row in rows]

    def update_record(
        self, case_id: str, username: str, expected_version: int, source_json: str
    ) -> SubmissionVersion | None:
        """Replace the current record if it is still at ``expected_version``; else ``None``."""
        statement = (
            update(submissions)
            .where(
                submissions.c.case_id == case_id,
                submissions.c.username == username,
                submissions.c.version == expected_version,
            )
            .values(
                source_json=source_json,
                version=submissions.c.version + 1,
                updated_at=func.now(),
            )
            .returning(
                submissions.c.case_id,
                submissions.c.submitted_at,
                submissions.c.updated_at,
                submissions.c.version,
            )
        )
        with self.engine.begin() as connection:
            row = connection.execute(statement).one_or_none()
        return None if row is None else SubmissionVersion(*row)

    def delete(self, case_id: str, username: str) -> bool:
        """Hard-delete one submission; ``False`` when none existed."""
        statement = delete(submissions).where(
            submissions.c.case_id == case_id, submissions.c.username == username
        )
        with self.engine.begin() as connection:
            deleted = connection.execute(statement).rowcount
        return deleted == 1

    def dispose(self) -> None:
        self.engine.dispose()
