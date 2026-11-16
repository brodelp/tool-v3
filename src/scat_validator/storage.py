"""PostgreSQL storage for submitted records (stage 2 spec §4, stage 3 spec §2).

Database entities live here, separate from the API contracts. Records are stored as the exact
validated JSON text; ``jsonb`` would rewrite number spellings, key order, and whitespace.
``source_json`` is the current record (what export returns); ``original_json`` is the record as
first submitted and never changes.
"""

import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Connection,
    DateTime,
    Engine,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    create_engine,
    delete,
    exists,
    func,
    or_,
    select,
    text,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import make_url
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.sql.dml import Delete, Update
from sqlalchemy.sql.expression import ScalarSelect

from scat_validator.config import (
    DB_CONNECT_TIMEOUT_SECONDS,
    JOB_STATUSES,
    MAX_CASE_ID_CHARS,
    MAX_USERNAME_CHARS,
)
from scat_validator.contracts import JobStatus

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

llm_proposals = Table(
    "llm_proposals",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column(
        "submission_id",
        BigInteger,
        ForeignKey("submissions.id", ondelete="CASCADE", name="llm_proposals_submission_id_fkey"),
        nullable=False,
    ),
    Column("set_id", Text, nullable=False),
    Column("set_json", Text, nullable=False),
    Column("based_on_version", BigInteger, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Index("ix_llm_proposals_submission_id", "submission_id"),
)

_STATUS_LIST = ", ".join(f"'{status}'" for status in JOB_STATUSES)

annotation_jobs = Table(
    "annotation_jobs",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("scope", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    Column("expires_at", DateTime(timezone=True), nullable=True),
    CheckConstraint("scope IN ('full', 'ae')", name="annotation_jobs_scope"),
)

annotation_job_items = Table(
    "annotation_job_items",
    metadata,
    Column(
        "job_id",
        Uuid,
        ForeignKey(
            "annotation_jobs.id", ondelete="CASCADE", name="annotation_job_items_job_id_fkey"
        ),
        primary_key=True,
    ),
    Column("index", Integer, primary_key=True),
    Column("case_id", Text, nullable=True),
    Column("username", Text, nullable=True),
    Column("status", Text, nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("not_before", DateTime(timezone=True), nullable=False),
    Column("record_json", Text, nullable=True),
    Column("result_json", Text, nullable=True),
    CheckConstraint(f"status IN ({_STATUS_LIST})", name="annotation_job_items_status"),
    Index("ix_annotation_job_items_status_not_before", "status", "not_before"),
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


@dataclass(frozen=True, slots=True)
class StoredProposal:
    """One unaccepted LLM annotation set (stage 4 spec §3)."""

    id: UUID
    set_id: str
    set_json: str
    based_on_version: int
    created_at: datetime
    expires_at: datetime


_PROPOSAL_COLUMNS = (
    llm_proposals.c.id,
    llm_proposals.c.set_id,
    llm_proposals.c.set_json,
    llm_proposals.c.based_on_version,
    llm_proposals.c.created_at,
    llm_proposals.c.expires_at,
)


class _NothingToAccept(Exception):
    """Raised inside a transaction to roll it back; carries no data."""


@dataclass(frozen=True, slots=True)
class NewJobItem:
    index: int
    case_id: str | None
    username: str | None
    status: Literal["pending", "rejected"]
    record_json: str | None
    result_json: str | None


@dataclass(frozen=True, slots=True)
class ClaimedItem:
    job_id: UUID
    index: int
    scope: str
    case_id: str
    username: str
    record_json: str
    attempts: int


@dataclass(frozen=True, slots=True)
class StoredJobItem:
    index: int
    case_id: str | None
    username: str | None
    status: JobStatus
    attempts: int
    result_json: str | None


@dataclass(frozen=True, slots=True)
class StoredJob:
    id: UUID
    scope: str
    created_at: datetime
    finished_at: datetime | None
    expires_at: datetime | None
    items: list[StoredJobItem]


def _finish_jobs(
    connection: Connection, now: datetime, ttl_seconds: int, job_id: UUID | None
) -> None:
    """Finish unfinished jobs (one, or all) that have no pending or running item left."""
    items = annotation_job_items
    still_open = exists().where(
        items.c.job_id == annotation_jobs.c.id, items.c.status.in_(("pending", "running"))
    )
    statement = (
        update(annotation_jobs)
        .where(annotation_jobs.c.finished_at.is_(None), ~still_open)
        .values(finished_at=now, expires_at=now + timedelta(seconds=ttl_seconds))
    )
    if job_id is not None:
        statement = statement.where(annotation_jobs.c.id == job_id)
    connection.execute(statement)


def _guarded_update(case_id: str, username: str, expected_version: int, source_json: str) -> Update:
    return (
        update(submissions)
        .where(
            submissions.c.case_id == case_id,
            submissions.c.username == username,
            submissions.c.version == expected_version,
        )
        .values(source_json=source_json, version=submissions.c.version + 1, updated_at=func.now())
        .returning(
            submissions.c.case_id,
            submissions.c.submitted_at,
            submissions.c.updated_at,
            submissions.c.version,
        )
    )


def _delete_proposal(proposal_id: UUID, case_id: str, username: str) -> Delete:
    return delete(llm_proposals).where(
        llm_proposals.c.id == proposal_id,
        llm_proposals.c.submission_id == _submission_id(case_id, username),
        llm_proposals.c.expires_at > func.now(),
    )


def _submission_id(case_id: str, username: str) -> ScalarSelect[int]:
    return (
        select(submissions.c.id)
        .where(submissions.c.case_id == case_id, submissions.c.username == username)
        .scalar_subquery()
    )


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

    def insert_annotated(
        self, case_id: str, username: str, original_json: str, source_json: str
    ) -> SubmissionVersion | None:
        """Store a new submission already updated once: ``original_json`` as submitted and
        ``source_json`` with an LLM set added, at version 2, as submitting and then accepting a
        proposal would leave it. ``None`` when ``(case_id, username)`` already exists."""
        statement = (
            insert(submissions)
            .values(
                case_id=case_id,
                username=username,
                source_json=source_json,
                original_json=original_json,
                version=2,
            )
            .on_conflict_do_nothing(index_elements=["case_id", "username"])
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
        statement = _guarded_update(case_id, username, expected_version, source_json)
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

    def insert_proposal(
        self,
        case_id: str,
        username: str,
        set_id: str,
        set_json: str,
        based_on_version: int,
        ttl_seconds: int,
    ) -> StoredProposal | None:
        """Store a proposal; ``None`` when the submission is gone. Purges expired proposals."""
        statement = (
            insert(llm_proposals)
            .values(
                id=uuid4(),
                submission_id=_submission_id(case_id, username),
                set_id=set_id,
                set_json=set_json,
                based_on_version=based_on_version,
                expires_at=func.now() + func.make_interval(0, 0, 0, 0, 0, 0, ttl_seconds),
            )
            .returning(*_PROPOSAL_COLUMNS)
        )
        with self.engine.begin() as connection:
            connection.execute(
                delete(llm_proposals).where(llm_proposals.c.expires_at <= func.now())
            )
            if connection.execute(select(_submission_id(case_id, username))).scalar() is None:
                return None
            row = connection.execute(statement).one()
        return StoredProposal(*row)

    def fetch_proposal(
        self, proposal_id: UUID, case_id: str, username: str
    ) -> StoredProposal | None:
        """An unexpired proposal of this submission, or ``None``."""
        statement = select(*_PROPOSAL_COLUMNS).where(
            llm_proposals.c.id == proposal_id,
            llm_proposals.c.submission_id == _submission_id(case_id, username),
            llm_proposals.c.expires_at > func.now(),
        )
        with self.engine.connect() as connection:
            row = connection.execute(statement).one_or_none()
        return None if row is None else StoredProposal(*row)

    def delete_proposal(self, proposal_id: UUID, case_id: str, username: str) -> bool:
        with self.engine.begin() as connection:
            return (
                connection.execute(_delete_proposal(proposal_id, case_id, username)).rowcount == 1
            )

    def accept_proposal(
        self,
        proposal_id: UUID,
        case_id: str,
        username: str,
        expected_version: int,
        source_json: str,
    ) -> SubmissionVersion | None:
        """Guarded record update plus proposal delete in one transaction; else ``None``."""
        update_statement = _guarded_update(case_id, username, expected_version, source_json)
        try:
            with self.engine.begin() as connection:
                row = connection.execute(update_statement).one_or_none()
                removed = connection.execute(_delete_proposal(proposal_id, case_id, username))
                if row is None or removed.rowcount != 1:
                    raise _NothingToAccept
        except _NothingToAccept:
            return None
        return SubmissionVersion(*row)

    def existing_identities(self, identities: Sequence[tuple[str, str]]) -> set[tuple[str, str]]:
        """The ``(case_id, username)`` pairs that already have a stored submission."""
        if not identities:
            return set()
        statement = select(submissions.c.case_id, submissions.c.username).where(
            tuple_(submissions.c.case_id, submissions.c.username).in_(list(identities))
        )
        with self.engine.connect() as connection:
            return {(row[0], row[1]) for row in connection.execute(statement)}

    def insert_job(
        self, scope: str, items: Sequence[NewJobItem], now: datetime, ttl_seconds: int
    ) -> UUID:
        """Store a job and its items; purges expired jobs. Job times come from the caller."""
        job_id = uuid4()
        finished = not any(item.status == "pending" for item in items)
        with self.engine.begin() as connection:
            connection.execute(delete(annotation_jobs).where(annotation_jobs.c.expires_at <= now))
            connection.execute(
                insert(annotation_jobs).values(
                    id=job_id,
                    scope=scope,
                    created_at=now,
                    finished_at=now if finished else None,
                    expires_at=now + timedelta(seconds=ttl_seconds) if finished else None,
                )
            )
            if items:
                connection.execute(
                    insert(annotation_job_items),
                    [
                        {
                            "job_id": job_id,
                            "index": item.index,
                            "case_id": item.case_id,
                            "username": item.username,
                            "status": item.status,
                            "attempts": 0,
                            "not_before": now,
                            "record_json": item.record_json,
                            "result_json": item.result_json,
                        }
                        for item in items
                    ],
                )
        return job_id

    def fetch_job(self, job_id: UUID, now: datetime) -> StoredJob | None:
        """An unexpired job with its items in index order, or ``None``."""
        job_statement = select(annotation_jobs).where(
            annotation_jobs.c.id == job_id,
            or_(annotation_jobs.c.expires_at.is_(None), annotation_jobs.c.expires_at > now),
        )
        items = annotation_job_items
        item_statement = (
            select(
                items.c.index,
                items.c.case_id,
                items.c.username,
                items.c.status,
                items.c.attempts,
                items.c.result_json,
            )
            .where(items.c.job_id == job_id)
            .order_by(items.c.index)
        )
        with self.engine.connect() as connection:
            job = connection.execute(job_statement).one_or_none()
            if job is None:
                return None
            rows = connection.execute(item_statement).all()
        return StoredJob(
            id=job.id,
            scope=job.scope,
            created_at=job.created_at,
            finished_at=job.finished_at,
            expires_at=job.expires_at,
            items=[StoredJobItem(*row) for row in rows],
        )

    def claim_item(self, now: datetime) -> ClaimedItem | None:
        """Atomically move the oldest claimable pending item to running, or ``None``."""
        items = annotation_job_items
        candidate = (
            select(items.c.job_id, items.c.index)
            .join(annotation_jobs, annotation_jobs.c.id == items.c.job_id)
            .where(items.c.status == "pending", items.c.not_before <= now)
            .order_by(annotation_jobs.c.created_at, items.c.job_id, items.c.index)
            .limit(1)
            .with_for_update(skip_locked=True, of=items)
        )
        scope = (
            select(annotation_jobs.c.scope)
            .where(annotation_jobs.c.id == items.c.job_id)
            .scalar_subquery()
        )
        statement = (
            update(items)
            .where(tuple_(items.c.job_id, items.c.index) == candidate.scalar_subquery())
            .values(status="running", attempts=items.c.attempts + 1)
            .returning(
                items.c.job_id,
                items.c.index,
                scope.label("scope"),
                items.c.case_id,
                items.c.username,
                items.c.record_json,
                items.c.attempts,
            )
        )
        with self.engine.begin() as connection:
            row = connection.execute(statement).one_or_none()
        return None if row is None else ClaimedItem(*row)

    def reschedule_item(self, job_id: UUID, index: int, not_before: datetime, refund: bool) -> None:
        """Return a running item to pending; ``refund`` gives the attempt back."""
        items = annotation_job_items
        statement = (
            update(items)
            .where(items.c.job_id == job_id, items.c.index == index, items.c.status == "running")
            .values(
                status="pending",
                not_before=not_before,
                attempts=items.c.attempts - 1 if refund else items.c.attempts,
            )
        )
        with self.engine.begin() as connection:
            connection.execute(statement)

    def finish_item(
        self,
        job_id: UUID,
        index: int,
        status: Literal["succeeded", "failed", "interrupted"],
        result_json: str | None,
        now: datetime,
        ttl_seconds: int,
    ) -> None:
        """Make a running item terminal, drop its record, and finish the job if it is done."""
        items = annotation_job_items
        statement = (
            update(items)
            .where(items.c.job_id == job_id, items.c.index == index, items.c.status == "running")
            .values(status=status, result_json=result_json, record_json=None)
        )
        with self.engine.begin() as connection:
            # Serialize finishers of one job: at READ COMMITTED two of them could otherwise each
            # see the other's item still running and leave the job unfinished.
            connection.execute(
                select(annotation_jobs.c.id).where(annotation_jobs.c.id == job_id).with_for_update()
            )
            if connection.execute(statement).rowcount == 1:
                _finish_jobs(connection, now, ttl_seconds, job_id)

    def interrupt_running(self, now: datetime, ttl_seconds: int) -> int:
        """Startup sweep: running items become interrupted; finished jobs get their times."""
        items = annotation_job_items
        statement = (
            update(items)
            .where(items.c.status == "running")
            .values(status="interrupted", record_json=None)
        )
        with self.engine.begin() as connection:
            count = connection.execute(statement).rowcount
            _finish_jobs(connection, now, ttl_seconds, None)
        return count

    def dispose(self) -> None:
        self.engine.dispose()
