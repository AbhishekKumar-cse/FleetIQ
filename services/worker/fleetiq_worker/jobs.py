"""Short SQL claims and fenced transactional effects with a global two-lease cap."""

import json
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid5

import sqlalchemy as sa
from fleetiq_domain.models.jobs import Job, JobResult
from fleetiq_domain.models.operations import AuditEvent, EventOutbox

LEASE_SECONDS = 60
MAX_CONCURRENCY = 2


class LeaseLost(RuntimeError):
    pass


@dataclass(frozen=True)
class Lease:
    job_id: UUID
    organization_id: UUID
    owner: str
    attempt: int
    job: dict


def _now(c, now):
    return now if now is not None else c.scalar(sa.select(sa.func.clock_timestamp()))


def _event(c, row, kind, reason):
    c.execute(
        sa.insert(EventOutbox).values(
            organization_id=row["organization_id"],
            scope_kind="owner",
            scope_id=row["owner_id"],
            kind="job." + kind,
            payload={"job_id": str(row["id"]), "attempt": row["attempt"]},
        )
    )
    c.execute(
        sa.insert(AuditEvent).values(
            organization_id=row["organization_id"],
            actor_id=row["owner_id"],
            action="job." + kind,
            target_kind="job",
            target_id=row["id"],
            scope_kind="owner",
            scope_id=row["owner_id"],
            versions={"attempt": row["attempt"], "worker": row.get("lease_owner")},
            reason=reason,
        )
    )


def claim(c, owner, *, now=None):
    if not owner or len(owner) > 128:
        raise ValueError("Bounded worker owner required")
    c.execute(
        sa.text("SELECT pg_advisory_xact_lock(hashtextextended('fleetiq-worker-capacity',0))")
    )
    at = _now(c, now)
    active = c.scalar(
        sa.select(sa.func.count())
        .select_from(Job)
        .where(Job.state == "running", Job.lease_expires_at > at)
    )
    if active >= MAX_CONCURRENCY:
        return None
    for _ in range(100):
        row = (
            c.execute(
                sa.select(Job.__table__)
                .where(
                    sa.or_(
                        sa.and_(Job.state == "pending", Job.available_at <= at),
                        sa.and_(Job.state == "running", Job.lease_expires_at <= at),
                    )
                )
                .order_by(Job.available_at, Job.created_at, Job.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        if row["attempt"] >= row["max_attempts"]:
            c.execute(
                sa.update(Job)
                .where(Job.id == row["id"], Job.organization_id == row["organization_id"])
                .values(
                    state="dead_letter",
                    error="Retry budget exhausted after expired lease",
                    lease_owner=None,
                    lease_expires_at=None,
                )
            )
            _event(c, row, "dead_letter", "Expired job exhausted its retry budget")
            continue
        attempt = row["attempt"] + 1
        c.execute(
            sa.update(Job)
            .where(Job.id == row["id"], Job.organization_id == row["organization_id"])
            .values(
                state="running",
                attempt=attempt,
                lease_owner=owner,
                lease_expires_at=at + timedelta(seconds=LEASE_SECONDS),
                error=None,
                progress=0,
                result=None,
            )
        )
        job = dict(row) | {"attempt": attempt, "state": "running", "lease_owner": owner}
        _event(c, job, "running", "Worker claimed durable job")
        return Lease(row["id"], row["organization_id"], owner, attempt, job)
    return None


def _owned(c, lease, now):
    row = (
        c.execute(
            sa.select(Job.__table__)
            .where(Job.id == lease.job_id, Job.organization_id == lease.organization_id)
            .with_for_update()
        )
        .mappings()
        .one()
    )
    at = _now(c, now)
    if (
        row["state"] != "running"
        or row["lease_owner"] != lease.owner
        or row["attempt"] != lease.attempt
        or row["lease_expires_at"] <= at
    ):
        raise LeaseLost("Job lease is no longer owned")
    return row, at


def heartbeat(c, lease, *, now=None):
    row, at = _owned(c, lease, now)
    c.execute(
        sa.update(Job)
        .where(Job.id == row["id"], Job.organization_id == lease.organization_id)
        .values(lease_expires_at=at + timedelta(seconds=LEASE_SECONDS))
    )


def finish(c, lease, result, *, unsupported=False, effect=None, now=None):
    """Future business writes must use effect(c) under this fenced result transaction."""
    if not isinstance(result, dict) or not result:
        raise ValueError("Explicit nonempty result required")
    if unsupported and result.get("status") != "unsupported":
        raise ValueError("Unsupported result must declare its status")
    json.dumps(result, allow_nan=False)
    with c.begin_nested():
        row, _ = _owned(c, lease, now)
        if effect is not None:
            effect(c)
        # Long transactional effects may expire: never acknowledge their unleased writes.
        _owned(c, lease, now)
        state = "unsupported" if unsupported else "completed"
        c.execute(
            sa.insert(JobResult).values(
                organization_id=lease.organization_id,
                job_id=lease.job_id,
                result_key=uuid5(lease.organization_id, str(lease.job_id)),
                attempt=lease.attempt,
                state=state,
                payload=result,
            )
        )
        c.execute(
            sa.update(Job)
            .where(Job.id == lease.job_id, Job.organization_id == lease.organization_id)
            .values(
                state=state,
                result=result,
                progress=0 if unsupported else 1,
                error="Handler is not implemented" if unsupported else None,
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        _event(
            c,
            row,
            state,
            "Explicit unsupported handler" if unsupported else "Durable result committed",
        )


def fail(c, lease, reason, *, now=None):
    row, at = _owned(c, lease, now)
    exhausted = row["attempt"] >= row["max_attempts"]
    state = "dead_letter" if exhausted else "pending"
    c.execute(
        sa.update(Job)
        .where(Job.id == lease.job_id, Job.organization_id == lease.organization_id)
        .values(
            state=state,
            error=reason[:512] or "Handler failed",
            lease_owner=None,
            lease_expires_at=None,
            available_at=at + timedelta(seconds=min(300, 2 ** row["attempt"])),
        )
    )
    _event(
        c,
        row,
        state,
        "Retry budget exhausted" if exhausted else "Retry scheduled with bounded backoff",
    )
