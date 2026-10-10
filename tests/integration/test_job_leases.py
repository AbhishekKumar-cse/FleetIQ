"""Real worker credentials: competing claims, fencing, retries and result atomicity."""

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from fleetiq_api.settings import Settings
from fleetiq_domain.models.assets import Organization
from fleetiq_domain.models.jobs import Job, JobResult
from fleetiq_domain.models.operations import EventOutbox, ImportBatch, User, enqueue_job
from fleetiq_domain.models.telemetry import Source
from fleetiq_worker import __main__ as runner
from fleetiq_worker.handlers import Outcome
from fleetiq_worker.jobs import LeaseLost, claim, fail, finish, heartbeat
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration


@pytest.fixture
def workers(isolated_database):
    migration = sa.create_engine(isolated_database[0], hide_parameters=True)
    with migration.begin() as c:
        org = c.scalar(
            sa.insert(Organization)
            .values(code="JOBS", name="Jobs fixture")
            .returning(Organization.id)
        )
        user = c.scalar(
            sa.insert(User)
            .values(
                organization_id=org,
                subject="owner",
                display_name="Owner",
                password_hash="$argon2id$fixture",
            )
            .returning(User.id)
        )
    worker = sa.create_engine(
        make_url(Settings().worker_database_url.get_secret_value()).set(
            database=make_url(isolated_database[0]).database
        ),
        hide_parameters=True,
        pool_size=4,
    )
    yield worker, migration, org, user
    worker.dispose()
    migration.dispose()


def request(fixture, key, *, kind="fixture", inputs=None):
    _, migration, org, user = fixture
    with migration.begin() as c:
        return enqueue_job(
            c,
            dict(
                organization_id=org,
                owner_id=user,
                kind=kind,
                input_hash=hashlib.sha256(key.encode()).hexdigest(),
                input=inputs or {},
                idempotency_key=key,
            ),
            actor_id=user,
            reason="Lease contract fixture",
        )


def test_competing_claims_global_bound_and_skip_locked(workers):
    worker, migration, _, _ = workers
    jobs = [request(workers, str(i)) for i in range(3)]
    barrier = threading.Barrier(2)

    def competing(owner):
        barrier.wait(timeout=5)
        with worker.begin() as c:
            return claim(c, owner)

    with ThreadPoolExecutor(max_workers=2) as pool:
        leases = list(pool.map(competing, ["a", "b"]))
    assert len({lease.job_id for lease in leases}) == 2
    with worker.begin() as c:
        assert claim(c, "third") is None
        finish(c, leases[0], {"status": "fixture_complete"})
    remaining = next(job for job in jobs if job not in {lease.job_id for lease in leases})
    # A separate process holds this row; SKIP LOCKED must not wait on it.
    with migration.begin() as locking:
        locking.execute(sa.select(Job.id).where(Job.id == remaining).with_for_update())
        with worker.begin() as c:
            c.execute(sa.text("SET LOCAL lock_timeout='1s'"))
            assert claim(c, "skipping") is None
    with worker.begin() as c:
        assert claim(c, "available").job_id == remaining


def test_heartbeat_expiry_and_stale_attempt_fencing(workers):
    worker, _, _, _ = workers
    job = request(workers, "expiry")
    now = datetime.now(UTC) + timedelta(seconds=1)
    with worker.begin() as c:
        first = claim(c, "same-owner", now=now)
        heartbeat(c, first, now=now + timedelta(seconds=40))
    with worker.begin() as c:
        assert claim(c, "other", now=now + timedelta(seconds=61)) is None
    with worker.begin() as c:
        second = claim(c, "same-owner", now=now + timedelta(seconds=101))
        assert second.job_id == job and second.attempt == 2
        for action in [heartbeat, lambda c, lease, **kw: finish(c, lease, {"stale": True}, **kw)]:
            with pytest.raises(LeaseLost), c.begin_nested():
                action(c, first, now=now + timedelta(seconds=102))
        finish(c, second, {"status": "recovered"}, now=now + timedelta(seconds=102))
    with worker.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(JobResult)) == 1
        assert c.scalar(sa.select(Job.result)) == {"status": "recovered"}


def test_retry_backoff_five_retries_then_dead_letter(workers):
    worker, _, _, _ = workers
    request(workers, "retries")
    now = datetime.now(UTC) + timedelta(seconds=1)
    for attempt in range(1, 7):
        with worker.begin() as c:
            lease = claim(c, "retrying", now=now)
            assert lease.attempt == attempt
            fail(c, lease, "Fixture transient failure", now=now)
        with worker.begin() as c:
            assert claim(c, "too-soon", now=now + timedelta(seconds=1)) is None
        now += timedelta(seconds=2**attempt)
    with worker.begin() as c:
        assert claim(c, "exhausted", now=now) is None
        row = c.execute(sa.select(Job.__table__)).mappings().one()
        assert row["attempt"] == row["max_attempts"] == 6
        assert row["state"] == "dead_letter" and row["lease_owner"] is None
        assert c.scalar(sa.select(sa.func.count()).select_from(JobResult)) == 0


def test_effect_and_result_rollback_and_unique_completion(workers):
    worker, _, org, user = workers
    request(workers, "effect")
    with worker.begin() as c:
        lease = claim(c, "effects")

    def effect(c):
        c.execute(
            sa.insert(EventOutbox).values(
                organization_id=org,
                scope_kind="owner",
                scope_id=user,
                kind="fixture.effect",
                payload={},
            )
        )

    def broken(c):
        effect(c)
        raise RuntimeError("Fault before durable result")

    with worker.begin() as c:
        with pytest.raises(RuntimeError):
            finish(c, lease, {"status": "fixture_complete"}, effect=broken)
        assert (
            c.scalar(
                sa.select(sa.func.count())
                .select_from(EventOutbox)
                .where(EventOutbox.kind == "fixture.effect")
            )
            == 0
        )
        finish(c, lease, {"status": "fixture_complete"}, effect=effect)
        with pytest.raises(LeaseLost):
            finish(c, lease, {"status": "fixture_complete"}, effect=effect)
    with worker.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(JobResult)) == 1
        assert (
            c.scalar(
                sa.select(sa.func.count())
                .select_from(EventOutbox)
                .where(EventOutbox.kind == "fixture.effect")
            )
            == 1
        )
        assert c.scalar(sa.text("SELECT has_table_privilege(current_user,'job','INSERT')"))
        with pytest.raises(DBAPIError, match="only prediction explanations"), c.begin_nested():
            c.execute(
                sa.insert(Job).values(
                    organization_id=org,
                    owner_id=user,
                    kind="fixture.forbidden",
                    input_hash="a" * 64,
                    input={},
                    idempotency_key="forbidden-worker-enqueue",
                )
            )
        assert not c.scalar(
            sa.text("SELECT has_table_privilege(current_user,'job_result','UPDATE')")
        )


def test_real_summary_and_explicit_unsupported(workers):
    worker, migration, org, _ = workers
    with migration.begin() as c:
        source = c.scalar(
            sa.insert(Source)
            .values(
                organization_id=org,
                code="S",
                source_kind="synthetic_engine",
                schema_version="synthetic-engine-v1",
            )
            .returning(Source.id)
        )
        batch = c.scalar(
            sa.insert(ImportBatch)
            .values(
                organization_id=org,
                source_id=source,
                checksum="a" * 64,
                kind="telemetry",
                state="completed",
                summary=dict(
                    processed=0,
                    accepted=0,
                    flagged=0,
                    rejected=0,
                    duplicate=0,
                    readings=0,
                    input_rows=0,
                ),
            )
            .returning(ImportBatch.id)
        )
    request(
        workers,
        "summary",
        kind="ingestion.summary",
        inputs={"batch_id": str(batch), "through_row": 0},
    )
    request(workers, "future", kind="future.model_training")
    assert runner.run(worker, concurrency=2, once=True) == {"completed": 1, "unsupported": 1}
    with worker.connect() as c:
        states = c.execute(sa.select(Job.state, Job.progress, Job.result)).all()
        assert any(
            state == "completed" and progress == 1 and result["status"] == "verified"
            for state, progress, result in states
        )
        assert any(
            state == "unsupported" and progress == 0 and result["status"] == "unsupported"
            for state, progress, result in states
        )
        assert c.scalar(sa.select(sa.func.count()).select_from(JobResult)) == 2


def test_runtime_heartbeat_and_graceful_drain(workers, monkeypatch):
    worker, _, _, _ = workers
    first = request(workers, "long")
    request(workers, "leave-pending")
    stop = threading.Event()
    initial, renewed_event = [], threading.Event()
    original_claim, original_heartbeat = runner.claim, runner.heartbeat

    def capture_claim(c, owner):
        lease = original_claim(c, owner)
        if lease:
            initial.append(c.scalar(sa.select(Job.lease_expires_at).where(Job.id == lease.job_id)))
        return lease

    def capture_heartbeat(c, lease):
        original_heartbeat(c, lease)
        renewed_event.set()

    def slow(c, job):
        stop.set()
        assert renewed_event.wait(timeout=10), "Heartbeat did not execute"
        deadline = time.monotonic() + 10
        while True:
            renewed = c.scalar(sa.select(Job.lease_expires_at).where(Job.id == job["id"]))
            if renewed > initial[0]:
                break
            assert time.monotonic() < deadline, "Heartbeat did not commit"
            time.sleep(0.01)
        return Outcome({"status": "heartbeat_verified"})

    monkeypatch.setattr(runner, "claim", capture_claim)
    monkeypatch.setattr(runner, "heartbeat", capture_heartbeat)
    monkeypatch.setattr(runner, "dispatch", slow)
    monkeypatch.setattr(runner, "HEARTBEAT_SECONDS", 0.05)
    assert runner.run(worker, concurrency=1, stop=stop) == {"completed": 1}
    with worker.connect() as c:
        assert c.scalar(sa.select(Job.state).where(Job.id == first)) == "completed"
        assert (
            c.scalar(sa.select(sa.func.count()).select_from(Job).where(Job.state == "pending")) == 1
        )
