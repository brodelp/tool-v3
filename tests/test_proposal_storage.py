"""Stage 4 spec §3: stored LLM proposals (table ``llm_proposals``, migration 0004)."""

from uuid import uuid4

from alembic import command
from sqlalchemy import create_engine, inspect, text

from scat_validator.storage import Storage, StoredProposal, sqlalchemy_url
from tests.conftest import alembic_config

RECORD = '{"case_id":"c1","username":"u","narrative":"x"}'
SET_JSON = '{"kind":"llm","annotations":{}}'


def seed(storage: Storage, case_id: str = "c1", username: str = "u") -> None:
    assert storage.insert(case_id, username, RECORD) is not None


def propose(storage: Storage, ttl: int = 3600, username: str = "u") -> StoredProposal | None:
    return storage.insert_proposal("c1", username, "llm:openai:m", SET_JSON, 1, ttl)


def test_insert_and_fetch_proposal(storage: Storage) -> None:
    seed(storage)
    stored = propose(storage)
    assert stored is not None
    assert (stored.set_id, stored.set_json, stored.based_on_version) == (
        "llm:openai:m",
        SET_JSON,
        1,
    )
    assert 3599 <= (stored.expires_at - stored.created_at).total_seconds() <= 3601
    assert storage.fetch_proposal(stored.id, "c1", "u") == stored


def test_insert_for_missing_submission_is_none(storage: Storage) -> None:
    assert propose(storage) is None


def test_fetch_is_scoped_to_submission(storage: Storage) -> None:
    seed(storage)
    seed(storage, username="other")
    stored = propose(storage)
    assert stored is not None
    assert storage.fetch_proposal(stored.id, "c1", "other") is None
    assert storage.fetch_proposal(stored.id, "nope", "u") is None
    assert storage.fetch_proposal(uuid4(), "c1", "u") is None


def test_expired_proposal_is_absent_and_purged(storage: Storage) -> None:
    seed(storage)
    expired = propose(storage, ttl=0)
    assert expired is not None
    assert storage.fetch_proposal(expired.id, "c1", "u") is None
    live = propose(storage)
    assert live is not None
    with storage.engine.connect() as connection:
        ids = connection.execute(text("SELECT id FROM llm_proposals")).scalars().all()
    assert ids == [live.id]


def test_delete_proposal(storage: Storage) -> None:
    seed(storage)
    stored = propose(storage)
    assert stored is not None
    assert storage.delete_proposal(stored.id, "c1", "other") is False
    assert storage.delete_proposal(stored.id, "c1", "u") is True
    assert storage.delete_proposal(stored.id, "c1", "u") is False
    assert storage.fetch_proposal(stored.id, "c1", "u") is None


def test_proposals_cascade_with_submission(storage: Storage) -> None:
    seed(storage)
    assert propose(storage) is not None
    assert storage.delete("c1", "u") is True
    with storage.engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM llm_proposals")).scalar_one() == 0


def test_accept_updates_record_and_deletes_proposal(storage: Storage) -> None:
    seed(storage)
    stored = propose(storage)
    assert stored is not None
    new = '{"case_id":"c1","username":"u","narrative":"y"}'
    result = storage.accept_proposal(stored.id, "c1", "u", 1, new)
    assert result is not None and result.version == 2
    fetched = storage.fetch("c1", "u")
    assert fetched is not None and fetched.source_json == new
    assert storage.fetch_proposal(stored.id, "c1", "u") is None


def test_accept_with_stale_version_changes_nothing(storage: Storage) -> None:
    seed(storage)
    stored = propose(storage)
    assert stored is not None
    assert storage.accept_proposal(stored.id, "c1", "u", 5, "{}") is None
    fetched = storage.fetch("c1", "u")
    assert fetched is not None and (fetched.source_json, fetched.version) == (RECORD, 1)
    assert storage.fetch_proposal(stored.id, "c1", "u") == stored


def test_accept_missing_or_expired_proposal_rolls_back_record(storage: Storage) -> None:
    seed(storage)
    expired = propose(storage, ttl=0)
    assert expired is not None
    assert storage.accept_proposal(expired.id, "c1", "u", 1, "{}") is None
    assert storage.accept_proposal(uuid4(), "c1", "u", 1, "{}") is None
    fetched = storage.fetch("c1", "u")
    assert fetched is not None and (fetched.source_json, fetched.version) == (RECORD, 1)


def test_migration_0004_round_trip(database_url: str) -> None:
    config = alembic_config(database_url)
    engine = create_engine(sqlalchemy_url(database_url))
    try:
        command.downgrade(config, "0003")
        assert "llm_proposals" not in inspect(engine).get_table_names()
        command.upgrade(config, "head")
        assert "llm_proposals" in inspect(engine).get_table_names()
        command.check(config)
    finally:
        engine.dispose()
        command.upgrade(config, "head")  # the session database stays at head for later tests
