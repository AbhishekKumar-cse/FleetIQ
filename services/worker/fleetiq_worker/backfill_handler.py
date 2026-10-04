"""Fence invalidation revisions with job completion; no unregistered model inference."""

from uuid import UUID, uuid5

import sqlalchemy as sa
from fleetiq_domain.models.backfill import AssessmentCursor, BackfillRequest
from fleetiq_domain.models.operations import AuditEvent
from fleetiq_domain.models.predictions import FeatureSnapshot, Prediction
from sqlalchemy.dialects.postgresql import insert as pg_insert


def revise(c, job):
    org = job["organization_id"]
    request_id = UUID(job["input"]["request_id"])
    if job["kind"] == "current.feature":
        request_id = (
            c.scalar(
                sa.select(AssessmentCursor.request_id).where(
                    AssessmentCursor.organization_id == org, AssessmentCursor.job_id == job["id"]
                )
            )
            or request_id
        )
    request = (
        c.execute(
            sa.select(BackfillRequest.__table__).where(
                BackfillRequest.id == request_id, BackfillRequest.organization_id == org
            )
        )
        .mappings()
        .one()
    )
    snapshots = (
        c.execute(
            sa.select(FeatureSnapshot.__table__)
            .where(
                FeatureSnapshot.organization_id == org,
                FeatureSnapshot.component_id == request["component_id"],
                FeatureSnapshot.as_of == request["as_of"],
                FeatureSnapshot.input_hash != request["input_hash"],
            )
            .order_by(FeatureSnapshot.created_at, FeatureSnapshot.id)
        )
        .mappings()
        .all()
    )
    latest = {}
    for old in snapshots:
        latest[(old["feature_version"], old["track"])] = old
    quality = {
        "supported": False,
        "reason": "Correction requires registered feature/model recomputation",
        "historical": request["historical"],
        "current_notifications": False,
    }
    for old in latest.values():
        feature = uuid5(request["id"], str(old["id"]))
        row = {k: v for k, v in old.items() if k not in {"id", "created_at"}}
        row.update(
            id=feature,
            input_hash=request["input_hash"],
            source_cutoff=request["source_cutoff"],
            supersedes_id=old["id"],
            vector={key: None for key in old["vector"]},
            quality=quality,
        )
        inserted = c.scalar(
            pg_insert(FeatureSnapshot)
            .values(**row)
            .on_conflict_do_nothing()
            .returning(FeatureSnapshot.id)
        )
        if inserted is None:
            continue
        for pred in c.execute(
            sa.select(Prediction.__table__).where(
                Prediction.organization_id == org, Prediction.feature_snapshot_id == old["id"]
            )
        ).mappings():
            updated = {k: v for k, v in pred.items() if k not in {"id", "created_at"}}
            updated.update(
                id=uuid5(feature, str(pred["id"])),
                feature_snapshot_id=feature,
                input_hash=request["input_hash"],
                source_cutoff=request["source_cutoff"],
                supersedes_id=pred["id"],
                output=None,
                uncertainty=None,
                coverage="unsupported",
                explanation_status="unsupported",
                quality=quality,
            )
            c.execute(pg_insert(Prediction).values(**updated).on_conflict_do_nothing())
    c.execute(
        sa.insert(AuditEvent).values(
            organization_id=org,
            actor_id=request["actor_id"],
            action="history.revised",
            target_kind="backfill_request",
            target_id=request["id"],
            scope_kind="organization",
            scope_id=org,
            versions={"source_cutoff": request["source_cutoff"].isoformat()},
            reason="Historical evidence retained; affected outputs explicitly unsupported",
        )
    )
