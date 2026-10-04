from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from fleetiq_data.backfill import queue_backfill, urgent_notification_allowed
from fleetiq_data.quality.time import classify_time
from fleetiq_domain.authorization import Principal
from fleetiq_domain.models.backfill import AssessmentCursor
from fleetiq_domain.models.operations import Job, Role, RoleAssignment
from fleetiq_domain.models.predictions import FeatureSnapshot, ModelDeployment, Prediction
from fleetiq_worker.backfill_handler import revise


def test_revision_repeat_coalescing_and_watermark(domain_connection):
    c, ids = domain_connection
    org = ids["organization"]
    role = c.scalar(
        sa.insert(Role)
        .values(organization_id=org, code="import", permissions=["dataset:import"])
        .returning(Role.id)
    )
    c.execute(
        sa.insert(RoleAssignment).values(
            organization_id=org, user_id=ids["user"], role_id=role, scope_kind="organization"
        )
    )
    principal = Principal(org, ids["user"])
    at = datetime(2026, 1, 2, tzinfo=UTC)
    feature = c.scalar(
        sa.insert(FeatureSnapshot)
        .values(
            organization_id=org,
            component_id=ids["component"],
            installation_id=ids["installation"],
            as_of=at,
            window_start=at - timedelta(hours=1),
            window_end=at,
            feature_version="fixture",
            input_hash="a" * 64,
            track="synthetic_engine_demo",
            vector={"temperature": 12},
            quality={},
        )
        .returning(FeatureSnapshot.id)
    )
    signature = dict(
        track="synthetic_engine_demo",
        task="anomaly",
        unit="score",
        horizon=0,
        feature_version="fixture",
        model_version="fixture",
        calibration_version="none",
        bundle_hash="b" * 64,
    )
    deployment = c.scalar(
        sa.insert(ModelDeployment)
        .values(
            organization_id=org, **signature, applicability={}, effective_at=at - timedelta(days=1)
        )
        .returning(ModelDeployment.id)
    )
    original = c.scalar(
        sa.insert(Prediction)
        .values(
            organization_id=org,
            component_id=ids["component"],
            feature_snapshot_id=feature,
            deployment_id=deployment,
            as_of=at,
            input_hash="a" * 64,
            **signature,
            output={"score": 0.2},
            quality={},
            coverage="full",
            explanation_status="pending",
        )
        .returning(Prediction.id)
    )
    kwargs = dict(
        component_id=ids["component"],
        as_of=at,
        source_cutoff=at + timedelta(hours=1),
        input_hash="c" * 64,
    )
    job = queue_backfill(c, principal, **kwargs)
    assert queue_backfill(c, principal, **kwargs) == job
    revise(c, c.execute(sa.select(Job.__table__).where(Job.id == job)).mappings().one())
    new = (
        c.execute(sa.select(Prediction.__table__).where(Prediction.supersedes_id == original))
        .mappings()
        .one()
    )
    assert new["output"] is None and new["coverage"] == "unsupported"
    assert new["source_cutoff"] == kwargs["source_cutoff"]
    assert c.scalar(sa.select(Prediction.output).where(Prediction.id == original)) == {"score": 0.2}
    current = queue_backfill(
        c, principal, **(kwargs | {"historical": False, "input_hash": "d" * 64})
    )
    assert (
        queue_backfill(c, principal, **(kwargs | {"historical": False, "input_hash": "e" * 64}))
        == current
    )
    assert c.scalar(sa.select(sa.func.count()).select_from(AssessmentCursor)) == 1
    assert classify_time(at - timedelta(seconds=120), at, now=at, latest=at) == "out_of_order"
    assert (
        classify_time(at - timedelta(seconds=121), at, now=at, latest=at)
        == "late_backfill_required"
    )
    assert not urgent_notification_allowed(
        historical=True, assessed_at=at, source_cutoff=at, now=at
    )
    assert urgent_notification_allowed(historical=False, assessed_at=at, source_cutoff=at, now=at)
    assert not urgent_notification_allowed(
        historical=False, assessed_at=at, source_cutoff=at, now=at + timedelta(seconds=121)
    )
