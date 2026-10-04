from datetime import UTC, datetime, timedelta

import pytest
from fleetiq_api.settings import Settings
from fleetiq_domain.models.operations import AuditEvent, EventOutbox, Job, enqueue_job
from sqlalchemy import create_engine, delete, func, insert, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError

pytestmark = pytest.mark.integration


def test_job_lease_bounds_and_atomic_outbox_audit(domain_connection):
    c, ids = domain_connection
    row = dict(
        organization_id=ids["organization"],
        owner_id=ids["user"],
        kind="fixture",
        input_hash="a" * 64,
        input={},
        idempotency_key="fixture",
    )
    when = datetime.now(UTC)
    for changes in (
        {"attempt": -1},
        {"attempt": 7},
        {"max_attempts": 11},
        {"state": "running"},
        {"lease_owner": "worker"},
        {"progress": float("nan")},
        {"state": "completed"},
        {"state": "failed"},
    ):
        with pytest.raises(IntegrityError), c.begin_nested():
            c.execute(insert(Job).values(**(row | changes)))
    c.execute(
        insert(Job).values(
            **(
                row
                | {
                    "idempotency_key": "leased",
                    "state": "running",
                    "attempt": 1,
                    "lease_owner": "demo-worker",
                    "lease_expires_at": when + timedelta(seconds=30),
                }
            )
        )
    )
    job = enqueue_job(c, row, actor_id=ids["user"], reason="fixture request")
    assert c.scalar(select(func.count()).select_from(EventOutbox)) == 1
    assert c.scalar(select(AuditEvent.target_id)) == job
    with pytest.raises(IntegrityError):
        enqueue_job(c, row | {"idempotency_key": "rollback"}, actor_id=ids["user"], reason="")
    assert c.scalar(select(func.count()).select_from(Job)) == 2
    assert c.scalar(select(func.count()).select_from(EventOutbox)) == 1


def test_application_cannot_delete_audit_or_read_source_secrets(isolated_database):
    url, _ = isolated_database
    app_url = make_url(Settings().database_url.get_secret_value()).set(database=url.database)
    engine = create_engine(app_url)
    try:
        with engine.begin() as c:
            with pytest.raises(DBAPIError), c.begin_nested():
                c.execute(delete(AuditEvent))
            assert not c.scalar(
                __import__("sqlalchemy").text(
                    "SELECT has_table_privilege(current_user,'source_credential','SELECT')"
                )
            )
            assert c.scalar(
                __import__("sqlalchemy").text(
                    "SELECT has_table_privilege(current_user,'audit_event','INSERT')"
                )
            )
    finally:
        engine.dispose()
