"""Revision 0003 (stage 4 spec §3): legacy ``annotations`` records move into ``annotation_sets``."""

import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from scat_validator.storage import DATABASE_URL_VARIABLE, sqlalchemy_url
from tests.conftest import alembic_config

LEGACY = (
    '{"case_id":"5-1","username":"u","narrative":"Aspirin x","dose":1.0E2,'
    '"annotations":{"SDrug":[{"text":"Aspirin","start":0,"end":7,"x":12345678901234567890}]}}'
)
MIGRATED = (
    '{"case_id":"5-1","username":"u","narrative":"Aspirin x","dose":1.0E2,'
    '"annotation_sets":{"user":{"kind":"user","annotations":'
    '{"SDrug":[{"text":"Aspirin","start":0,"end":7,"x":12345678901234567890}]}}}}'
)
PLAIN = '{"case_id":"6-1","username":"u","narrative":"Plain" , "dose":1.0E2}'
WITH_LLM = (
    '{"case_id":"7-1","username":"u","narrative":"Aspirin x","annotation_sets" : {'
    '"llm:openai:m":{"kind":"llm","provider":"openai","model":"m",'
    '"generated_at":"2026-10-29T00:00:00Z","annotations":{}},'
    '"user":{"kind":"user","annotations":{"AE":[]}}}}'
)
LLM_ONLY = (
    '{"case_id":"8-1","username":"u","narrative":"x","annotation_sets":{'
    '"llm:openai:m":{"kind":"llm","provider":"openai","model":"m",'
    '"generated_at":"2026-10-29T00:00:00Z","annotations":{}}}}'
)
INSERT = (
    "INSERT INTO submissions (case_id, username, source_json, original_json, version, "
    "submitted_at, updated_at) VALUES (:c, 'u', :s, :o, 3, '2026-10-23T00:00:00Z', "
    "'2026-10-24T00:00:00Z')"
)


@pytest.fixture
def legacy_engine() -> Iterator[Engine]:
    """A throwaway database migrated to 0002 only (the session database is at head)."""
    base_url = os.environ.get(DATABASE_URL_VARIABLE)
    if not base_url:
        pytest.fail(f"{DATABASE_URL_VARIABLE} is not set; open the dev container (README.md).")
    name = f"scat_mig_{uuid4().hex[:12]}"
    admin = create_engine(sqlalchemy_url(base_url), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(base_url).set(database=name).render_as_string(hide_password=False)
    engine = create_engine(sqlalchemy_url(url))
    engine.url_string = url  # type: ignore[attr-defined]
    try:
        command.upgrade(alembic_config(url), "0002")
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def url_of(engine: Engine) -> str:
    return str(engine.url_string)  # type: ignore[attr-defined]


def insert(engine: Engine, case_id: str, source: str, original: str | None = None) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(INSERT), {"c": case_id, "s": source, "o": original if original else source}
        )


def rows(engine: Engine) -> dict[str, tuple[object, ...]]:
    with engine.connect() as connection:
        found = connection.execute(
            text("SELECT case_id, source_json, original_json, version, updated_at FROM submissions")
        )
        return {row[0]: tuple(row[1:]) for row in found}


def test_0003_upgrade_splices_legacy_records(legacy_engine: Engine) -> None:
    insert(legacy_engine, "5-1", LEGACY)
    insert(legacy_engine, "6-1", PLAIN)
    before = rows(legacy_engine)
    command.upgrade(alembic_config(url_of(legacy_engine)), "0003")
    after = rows(legacy_engine)
    assert after["5-1"] == (MIGRATED, LEGACY, before["5-1"][2], before["5-1"][3])
    assert after["6-1"] == before["6-1"]


def test_0003_downgrade_restores_user_map_and_drops_llm_sets(legacy_engine: Engine) -> None:
    config = alembic_config(url_of(legacy_engine))
    insert(legacy_engine, "5-1", LEGACY)
    command.upgrade(config, "0003")
    insert(legacy_engine, "6-1", PLAIN)
    insert(legacy_engine, "7-1", WITH_LLM)
    insert(legacy_engine, "8-1", LLM_ONLY)
    before = rows(legacy_engine)
    command.downgrade(config, "0002")
    after = rows(legacy_engine)
    assert after["5-1"][0] == LEGACY
    assert after["6-1"] == before["6-1"]
    assert after["7-1"][0] == (
        '{"case_id":"7-1","username":"u","narrative":"Aspirin x","annotations":{"AE":[]}}'
    )
    assert after["8-1"][0] == '{"case_id":"8-1","username":"u","narrative":"x"}'
    for case_id in ("5-1", "7-1", "8-1"):
        assert after[case_id][1:] == before[case_id][1:]


def test_0003_upgrade_then_check_is_clean(legacy_engine: Engine) -> None:
    config = alembic_config(url_of(legacy_engine))
    command.upgrade(config, "head")
    command.check(config)


FOREIGN_BOTH = (
    '{"case_id":"9-1","username":"u","narrative":"x","annotations":{"AE":[]},'
    '"annotation_sets":{"note":"foreign"}}'
)
FOREIGN_ONLY = '{"case_id":"9-2","username":"u","narrative":"x","annotation_sets":[1,2]}'


@pytest.mark.parametrize("foreign", [FOREIGN_BOTH, FOREIGN_ONLY])
def test_0003_refuses_records_with_foreign_annotation_sets(
    legacy_engine: Engine, foreign: str
) -> None:
    insert(legacy_engine, "5-1", LEGACY)
    insert(legacy_engine, "6-1", PLAIN)
    insert(legacy_engine, "9-1", foreign)
    before = rows(legacy_engine)
    with pytest.raises(RuntimeError) as raised:
        command.upgrade(alembic_config(url_of(legacy_engine)), "0003")
    assert str(raised.value) == (
        "migration 0003: 1 stored record(s) already have a top-level annotation_sets member; "
        "resolve them before upgrading"
    )
    assert "foreign" not in str(raised.value)
    assert rows(legacy_engine) == before


NESTED_DECOY = (
    '{"case_id":"10-1","username":"u","narrative":"x",'
    '"meta":{"annotations":{"AE":[]},"annotation_sets":{}}}'
)
ESCAPED_NAME = (
    '{"case_id":"10-2","username":"u","narrative":"x",'
    '"annot\\u0061tions" : {"AE":[{"text":"x","start":0,"end":1}]} }'
)
ESCAPED_MIGRATED = (
    '{"case_id":"10-2","username":"u","narrative":"x",'
    '"annotation_sets":{"user":{"kind":"user","annotations":'
    '{"AE":[{"text":"x","start":0,"end":1}]}}} }'
)


def test_0003_ignores_nested_decoys_and_decodes_escaped_names(legacy_engine: Engine) -> None:
    insert(legacy_engine, "10-1", NESTED_DECOY)
    insert(legacy_engine, "10-2", ESCAPED_NAME)
    before = rows(legacy_engine)
    command.upgrade(alembic_config(url_of(legacy_engine)), "0003")
    after = rows(legacy_engine)
    assert after["10-1"] == before["10-1"]
    assert after["10-2"] == (ESCAPED_MIGRATED, *before["10-2"][1:])
