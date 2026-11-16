"""Stage 4 spec §3 and §4.10: stored annotation jobs (migration 0005)."""

import threading
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import text

from scat_validator.storage import NewJobItem, Storage

NOW = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)
TTL = 86_400


def pending(index: int, case_id: str | None = None) -> NewJobItem:
    case = case_id or f"c{index}"
    return NewJobItem(index, case, "u", "pending", f'{{"i":{index}}}', None)


def rejected(index: int) -> NewJobItem:
    return NewJobItem(index, None, None, "rejected", None, '{"valid":false}')


def record_of(storage: Storage, job_id: UUID, index: int) -> str | None:
    with storage.engine.connect() as connection:
        return connection.execute(
            text("SELECT record_json FROM annotation_job_items WHERE job_id=:j AND index=:i"),
            {"j": job_id, "i": index},
        ).scalar()


def counts(storage: Storage) -> tuple[int, int]:
    with storage.engine.connect() as connection:
        jobs = connection.execute(text("SELECT count(*) FROM annotation_jobs")).scalar_one()
        items = connection.execute(text("SELECT count(*) FROM annotation_job_items")).scalar_one()
    return jobs, items


def test_insert_and_fetch_round_trip(storage: Storage) -> None:
    job_id = storage.insert_job("full", [rejected(1), pending(0)], NOW, TTL)
    job = storage.fetch_job(job_id, NOW)
    assert job is not None
    assert (job.id, job.scope, job.created_at) == (job_id, "full", NOW)
    assert job.finished_at is None and job.expires_at is None
    assert [
        (i.index, i.case_id, i.username, i.status, i.attempts, i.result_json) for i in job.items
    ] == [
        (0, "c0", "u", "pending", 0, None),
        (1, None, None, "rejected", 0, '{"valid":false}'),
    ]
    assert storage.fetch_job(UUID(int=1), NOW) is None


def test_all_rejected_job_is_finished_at_once(storage: Storage) -> None:
    job = storage.fetch_job(storage.insert_job("ae", [rejected(0), rejected(1)], NOW, TTL), NOW)
    assert job is not None
    assert job.finished_at == NOW
    assert job.expires_at == NOW + timedelta(seconds=TTL)


def test_claim_order_and_skip(storage: Storage) -> None:
    first = storage.insert_job("full", [pending(0), pending(1)], NOW, TTL)
    later = NOW + timedelta(seconds=1)
    second = storage.insert_job("ae", [pending(0, "d0")], later, TTL)
    delayed = storage.insert_job("full", [pending(0, "e0")], later, TTL)
    with storage.engine.begin() as connection:
        connection.execute(
            text("UPDATE annotation_job_items SET not_before=:t WHERE job_id=:j"),
            {"t": later + timedelta(seconds=60), "j": delayed},
        )
    claims = [storage.claim_item(later) for _ in range(3)]
    assert [(c.job_id, c.index) for c in claims if c] == [(first, 0), (first, 1), (second, 0)]
    assert [c.scope for c in claims if c] == ["full", "full", "ae"]
    assert all(c.attempts == 1 for c in claims if c)
    assert (claims[0] and claims[0].record_json) == '{"i":0}'
    assert storage.claim_item(later) is None
    claimed = storage.claim_item(later + timedelta(seconds=60))
    assert claimed is not None and claimed.job_id == delayed
    job = storage.fetch_job(first, later)
    assert job is not None and [i.status for i in job.items] == ["running", "running"]


def test_reschedule_refund(storage: Storage) -> None:
    job_id = storage.insert_job("full", [pending(0)], NOW, TTL)
    claimed = storage.claim_item(NOW)
    assert claimed is not None and claimed.attempts == 1
    later = NOW + timedelta(seconds=30)
    storage.reschedule_item(job_id, 0, later, refund=True)
    job = storage.fetch_job(job_id, NOW)
    assert job is not None
    assert (job.items[0].status, job.items[0].attempts) == ("pending", 0)
    assert storage.claim_item(NOW) is None
    again = storage.claim_item(later)
    assert again is not None and again.attempts == 1
    storage.reschedule_item(job_id, 0, later, refund=False)
    job = storage.fetch_job(job_id, NOW)
    assert job is not None and (job.items[0].status, job.items[0].attempts) == ("pending", 1)


def test_finish_last_item_finishes_job_and_clears_record(storage: Storage) -> None:
    job_id = storage.insert_job("full", [pending(0), pending(1)], NOW, TTL)
    assert storage.claim_item(NOW) and storage.claim_item(NOW)
    done = NOW + timedelta(seconds=5)
    storage.finish_item(job_id, 0, "succeeded", None, done, TTL)
    assert record_of(storage, job_id, 0) is None
    assert record_of(storage, job_id, 1) == '{"i":1}'
    job = storage.fetch_job(job_id, done)
    assert job is not None and job.finished_at is None and job.expires_at is None
    last = NOW + timedelta(seconds=9)
    storage.finish_item(job_id, 1, "failed", '{"code":"X"}', last, TTL)
    job = storage.fetch_job(job_id, last)
    assert job is not None
    assert job.finished_at == last and job.expires_at == last + timedelta(seconds=TTL)
    assert [(i.status, i.result_json) for i in job.items] == [
        ("succeeded", None),
        ("failed", '{"code":"X"}'),
    ]
    assert record_of(storage, job_id, 1) is None


def test_expired_job_reads_as_absent_and_is_purged(storage: Storage) -> None:
    job_id = storage.insert_job("full", [rejected(0)], NOW, TTL)
    expires = NOW + timedelta(seconds=TTL)
    assert storage.fetch_job(job_id, expires - timedelta(seconds=1)) is not None
    assert storage.fetch_job(job_id, expires) is None
    assert counts(storage) == (1, 1)
    storage.insert_job("full", [pending(0)], expires, TTL)
    assert counts(storage) == (1, 1)
    assert storage.fetch_job(job_id, NOW) is None


def test_interrupt_running(storage: Storage) -> None:
    job_id = storage.insert_job("full", [pending(0), pending(1)], NOW, TTL)
    assert storage.claim_item(NOW) is not None
    later = NOW + timedelta(seconds=10)
    assert storage.interrupt_running(later, TTL) == 1
    job = storage.fetch_job(job_id, later)
    assert job is not None
    assert [i.status for i in job.items] == ["interrupted", "pending"]
    assert job.finished_at is None
    assert record_of(storage, job_id, 0) is None
    assert record_of(storage, job_id, 1) == '{"i":1}'
    assert storage.interrupt_running(later, TTL) == 0


def test_interrupt_running_finishes_jobs_without_open_items(storage: Storage) -> None:
    job_id = storage.insert_job("full", [pending(0)], NOW, TTL)
    assert storage.claim_item(NOW) is not None
    later = NOW + timedelta(seconds=10)
    assert storage.interrupt_running(later, TTL) == 1
    job = storage.fetch_job(job_id, later)
    assert job is not None
    assert job.finished_at == later and job.expires_at == later + timedelta(seconds=TTL)


def test_existing_identities(storage: Storage) -> None:
    assert storage.insert("c1", "u", '{"case_id":"c1","username":"u"}') is not None
    assert storage.insert("c2", "v", '{"case_id":"c2","username":"v"}') is not None
    asked = [("c1", "u"), ("c1", "v"), ("c2", "v"), ("c3", "u")]
    assert storage.existing_identities(asked) == {("c1", "u"), ("c2", "v")}
    assert storage.existing_identities([]) == set()


def test_finish_after_interrupt_leaves_item_interrupted(storage: Storage) -> None:
    job_id = storage.insert_job("full", [pending(0), pending(1)], NOW, TTL)
    assert storage.claim_item(NOW) is not None
    assert storage.interrupt_running(NOW, TTL) == 1
    storage.finish_item(job_id, 0, "succeeded", None, NOW, TTL)
    job = storage.fetch_job(job_id, NOW)
    assert job is not None and [i.status for i in job.items] == ["interrupted", "pending"]
    assert job.finished_at is None


def test_concurrent_finishers_finish_the_job(storage: Storage) -> None:
    job_id = storage.insert_job("full", [pending(0), pending(1)], NOW, TTL)
    assert storage.claim_item(NOW) and storage.claim_item(NOW)
    done = NOW + timedelta(seconds=5)
    other = threading.Thread(
        target=storage.finish_item, args=(job_id, 0, "succeeded", None, done, TTL)
    )
    with storage.engine.connect() as first:
        # The first finisher holds the job lock with its own item done but uncommitted.
        first.execute(
            text("SELECT id FROM annotation_jobs WHERE id = :j FOR UPDATE"), {"j": job_id}
        )
        first.execute(
            text(
                "UPDATE annotation_job_items SET status='succeeded', record_json=NULL "
                "WHERE job_id=:j AND index=1"
            ),
            {"j": job_id},
        )
        other.start()
        deadline = time.monotonic() + 10
        blocked = False
        while time.monotonic() < deadline and not blocked:
            with storage.engine.connect() as probe:
                blocked = bool(
                    probe.execute(
                        text(
                            "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock' "
                            "AND datname = current_database()"
                        )
                    ).scalar_one()
                )
            if not blocked:
                time.sleep(0.02)
        assert blocked, "second finisher did not wait for the job lock"
        first.execute(
            text("UPDATE annotation_jobs SET finished_at=NULL WHERE id=:j"),
            {"j": job_id},
        )
        first.commit()
    other.join(timeout=10)
    assert not other.is_alive()
    job = storage.fetch_job(job_id, done)
    assert job is not None and job.finished_at == done
    assert [i.status for i in job.items] == ["succeeded", "succeeded"]
