from datetime import UTC, datetime, timedelta

import pytest
from fleetiq_domain.models.predictions import (
    FeatureSnapshot,
    HealthScore,
    ModelDeployment,
    PolicyVersion,
    Prediction,
)
from sqlalchemy import insert, update
from sqlalchemy.exc import DBAPIError, IntegrityError

pytestmark = pytest.mark.integration


@pytest.fixture
def prediction_fixture(domain_connection):
    c, ids = domain_connection
    when = datetime(2026, 1, 2, tzinfo=UTC)
    feature = c.scalar(
        insert(FeatureSnapshot)
        .values(
            organization_id=ids["organization"],
            component_id=ids["component"],
            installation_id=ids["installation"],
            as_of=when,
            window_start=when - timedelta(hours=1),
            window_end=when,
            track="synthetic_engine_demo",
            feature_version="v1",
            input_hash="a" * 64,
            vector={"vibration": 1.0},
            quality={},
        )
        .returning(FeatureSnapshot.id)
    )
    signature = dict(
        track="synthetic_engine_demo",
        task="failure_risk",
        unit="operating_hours",
        horizon=24,
        feature_version="v1",
        model_version="m1",
        calibration_version="c1",
        bundle_hash="b" * 64,
    )
    deployment = c.scalar(
        insert(ModelDeployment)
        .values(
            organization_id=ids["organization"],
            **signature,
            applicability={},
            effective_at=when - timedelta(days=1),
        )
        .returning(ModelDeployment.id)
    )
    row = dict(
        organization_id=ids["organization"],
        component_id=ids["component"],
        feature_snapshot_id=feature,
        deployment_id=deployment,
        as_of=when,
        input_hash="a" * 64,
        **signature,
        quality={},
        ood=False,
        coverage="full",
        output={"probability": 0.2},
        explanation_status="pending",
    )
    return c, ids, row


def test_signature_provenance_duplicates_and_immutability(prediction_fixture):
    c, _, row = prediction_fixture
    prediction = c.scalar(insert(Prediction).values(**row).returning(Prediction.id))
    for changes in (
        {"unit": "cycles"},
        {"horizon": 48},
        {"input_hash": "c" * 64},
        {"model_version": "m2"},
        {"as_of": row["as_of"] + timedelta(hours=1)},
    ):
        with pytest.raises(DBAPIError), c.begin_nested():
            c.execute(insert(Prediction).values(**(row | changes)))
    with pytest.raises(IntegrityError), c.begin_nested():
        c.execute(insert(Prediction).values(**row))
    with pytest.raises(DBAPIError), c.begin_nested():
        c.execute(
            update(Prediction).where(Prediction.id == prediction).values(output={"probability": 0})
        )


def test_unsupported_health_is_unknown_not_zero(prediction_fixture):
    c, ids, row = prediction_fixture
    c.execute(
        insert(Prediction).values(
            **(row | {"coverage": "unsupported", "output": None, "ood": True})
        )
    )
    policy = c.scalar(
        insert(PolicyVersion)
        .values(
            organization_id=ids["organization"],
            code="demo",
            version="1",
            content_hash="d" * 64,
            applicability={},
            effective_at=row["as_of"],
        )
        .returning(PolicyVersion.id)
    )
    health = dict(
        organization_id=ids["organization"],
        aircraft_id=ids["aircraft"],
        as_of=row["as_of"],
        policy_version_id=policy,
        input_hash="a" * 64,
        coverage="unsupported",
        category="unknown",
        score=None,
    )
    c.execute(insert(HealthScore).values(**health))
    for changes in ({"score": 0}, {"score": float("nan")}, {"category": "healthy"}):
        with pytest.raises(IntegrityError), c.begin_nested():
            c.execute(insert(HealthScore).values(**(health | changes)))
