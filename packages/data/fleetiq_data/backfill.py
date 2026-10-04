"""Queue corrections without rewriting immutable histories or issuing live alerts."""

from datetime import timedelta
from uuid import uuid4

import sqlalchemy as sa
from fleetiq_domain.authorization import require
from fleetiq_domain.models.backfill import AssessmentCursor, BackfillRequest
from fleetiq_domain.models.operations import AuditEvent, Job, enqueue_job
from sqlalchemy.dialects.postgresql import insert as pg_insert

from fleetiq_data.quality.time import utc


def urgent_notification_allowed(*, historical, assessed_at, source_cutoff, now):
    age = utc(now) - utc(assessed_at)
    return (
        not historical
        and timedelta(0) <= age <= timedelta(seconds=120)
        and utc(assessed_at) <= utc(source_cutoff) <= utc(now)
    )


def queue_backfill(
    c, principal, *, component_id, as_of, source_cutoff, input_hash, historical=True
):
    require(c, principal, "dataset:import")
    org = principal.organization_id
    # Serializes both duplicate request creation and current-pointer coalescing.
    c.execute(
        sa.text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"assessment:{org}:{component_id}"},
    )
    request = c.scalar(
        pg_insert(BackfillRequest)
        .values(
            organization_id=org,
            component_id=component_id,
            as_of=utc(as_of),
            source_cutoff=utc(source_cutoff),
            input_hash=input_hash,
            historical=historical,
            actor_id=principal.user_id,
        )
        .on_conflict_do_nothing()
        .returning(BackfillRequest.id)
    )
    if request is None:
        request = c.scalar(
            sa.select(BackfillRequest.id).where(
                BackfillRequest.organization_id == org,
                BackfillRequest.component_id == component_id,
                BackfillRequest.as_of == utc(as_of),
                BackfillRequest.input_hash == input_hash,
                BackfillRequest.historical == historical,
            )
        )
        return c.scalar(
            sa.select(Job.id).where(Job.organization_id == org, Job.idempotency_key == str(request))
        ) or c.scalar(
            sa.select(AssessmentCursor.job_id).where(
                AssessmentCursor.organization_id == org,
                AssessmentCursor.component_id == component_id,
            )
        )
    cursor = (
        c.execute(
            sa.select(AssessmentCursor.__table__, Job.state)
            .join(Job, Job.id == AssessmentCursor.job_id)
            .where(
                AssessmentCursor.organization_id == org,
                AssessmentCursor.component_id == component_id,
            )
            .with_for_update()
        )
        .mappings()
        .first()
        if not historical
        else None
    )
    if cursor and cursor["state"] == "pending":
        job_id = cursor["job_id"]
        c.execute(
            sa.update(AssessmentCursor)
            .where(AssessmentCursor.id == cursor["id"])
            .values(request_id=request)
        )
    else:
        job_id = uuid4()
        enqueue_job(
            c,
            dict(
                id=job_id,
                organization_id=org,
                owner_id=principal.user_id,
                kind="history.backfill" if historical else "current.feature",
                input_hash=input_hash,
                idempotency_key=str(request),
                input={"request_id": str(request), "component_id": str(component_id)},
            ),
            actor_id=principal.user_id,
            reason="Historical correction" if historical else "Fresh assessment request",
        )
        if not historical:
            c.execute(
                pg_insert(AssessmentCursor)
                .values(
                    organization_id=org,
                    component_id=component_id,
                    request_id=request,
                    job_id=job_id,
                )
                .on_conflict_do_update(
                    index_elements=["organization_id", "component_id"],
                    set_={"request_id": request, "job_id": job_id},
                )
            )
    c.execute(
        sa.insert(AuditEvent).values(
            organization_id=org,
            actor_id=principal.user_id,
            action="history.correct",
            target_kind="backfill_request",
            target_id=request,
            scope_kind="organization",
            scope_id=org,
            versions={"input_hash": input_hash, "source_cutoff": utc(source_cutoff).isoformat()},
            reason="Retained historical correction"
            if historical
            else "Coalesced current assessment",
        )
    )
    return job_id
