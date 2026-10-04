from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fleetiq_api.settings import Settings
from fleetiq_domain.models.fleet import (
    AircraftStatusEvent,
    FleetSnapshot,
    ResourceCalendar,
    Scenario,
)
from sqlalchemy import create_engine, insert, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError

pytestmark = pytest.mark.integration


def test_projection_provenance_and_exclusive_counts(domain_connection):
    c, ids = domain_connection
    when = datetime(2026, 1, 2, tzinfo=UTC)
    row = dict(
        organization_id=ids["organization"],
        fleet_id=ids["fleet"],
        as_of=when,
        source_cutoff=when,
        scope="recorded",
        counts={"serviceable": 1, "maintenance": 0, "grounded_other": 0, "unknown": 0},
        total=1,
        serviceable=1,
        maintenance=0,
        grounded_other=0,
        unknown=0,
        inventory_state={},
        configuration_hash="a" * 64,
        versions={},
    )
    baseline = c.scalar(insert(FleetSnapshot).values(**row).returning(FleetSnapshot.id))
    for changes in (
        {"scope": "projected"},
        {"total": 2},
        {"source_cutoff": when + timedelta(hours=1)},
    ):
        with pytest.raises(IntegrityError), c.begin_nested():
            c.execute(insert(FleetSnapshot).values(**(row | changes)))
    scenario = c.scalar(
        insert(Scenario)
        .values(
            organization_id=ids["organization"],
            snapshot_id=baseline,
            owner_id=uuid4(),
            assumptions={},
            changes={},
            configuration_hash="b" * 64,
            unit_mapping={},
            horizon_slots=24,
            replicates=2,
        )
        .returning(Scenario.id)
    )
    c.execute(
        insert(FleetSnapshot).values(**(row | {"scope": "projected", "scenario_id": scenario}))
    )
    with pytest.raises(IntegrityError), c.begin_nested():
        c.execute(
            insert(AircraftStatusEvent).values(
                organization_id=ids["organization"],
                aircraft_id=ids["aircraft"],
                occurred_at=when,
                recorded_at=when,
                status="serviceable",
                reason="simulated release",
                actor_id=uuid4(),
                version=1,
                origin="scenario",
            )
        )
    assert c.scalar(select(AircraftStatusEvent.id)) is None
    with pytest.raises(DBAPIError), c.begin_nested():
        c.execute(
            insert(ResourceCalendar).values(
                organization_id=ids["organization"],
                site_id=ids["site"],
                slot_start=when,
                slot_end=when + timedelta(hours=1),
                bay_capacity=2,
                version=1,
                skill_pools={"mechanical": ["same-worker"], "inspection": ["same-worker"]},
            )
        )


def test_worker_role_cannot_mutate_live_status(isolated_database):
    url, _ = isolated_database
    worker = make_url(Settings().worker_database_url.get_secret_value()).set(database=url.database)
    engine = create_engine(worker)
    try:
        with engine.begin() as c:
            with pytest.raises(DBAPIError), c.begin_nested():
                c.execute(
                    insert(AircraftStatusEvent).values(
                        organization_id=uuid4(),
                        aircraft_id=uuid4(),
                        occurred_at=datetime.now(UTC),
                        recorded_at=datetime.now(UTC),
                        status="serviceable",
                        reason="simulation",
                        actor_id=uuid4(),
                        version=0,
                    )
                )
    finally:
        engine.dispose()
