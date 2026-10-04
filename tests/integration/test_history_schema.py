from datetime import UTC, datetime, timedelta

import pytest
from fleetiq_domain.models.history import FailureEvent, Flight, MaintenanceEvent
from sqlalchemy import insert, update
from sqlalchemy.exc import DBAPIError, IntegrityError

pytestmark = pytest.mark.integration


def test_invalid_durations_confirmation_and_immutable_late_history(domain_connection):
    c, ids = domain_connection
    now = datetime(2026, 1, 2, tzinfo=UTC)
    with pytest.raises(IntegrityError), c.begin_nested():
        c.execute(
            insert(Flight),
            dict(
                organization_id=ids["organization"],
                aircraft_id=ids["aircraft"],
                start=now,
                end=now - timedelta(hours=1),
                duration_hours=-1,
                flight_cycles=1,
                landing_cycles=1,
            ),
        )
    with pytest.raises(IntegrityError), c.begin_nested():
        c.execute(
            insert(FailureEvent),
            dict(
                organization_id=ids["organization"],
                component_id=ids["component"],
                observed_at=now,
                recorded_at=now,
                mode="DEMO",
                eligibility="confirmed",
            ),
        )
    row = dict(
        organization_id=ids["organization"],
        aircraft_id=ids["aircraft"],
        component_id=ids["component"],
        occurred_at=now,
        recorded_at=now + timedelta(days=1),
        action="inspection",
        actor_id=ids["user"],
    )
    first = c.scalar(insert(MaintenanceEvent).values(**row).returning(MaintenanceEvent.id))
    with pytest.raises(DBAPIError), c.begin_nested():
        c.execute(
            update(MaintenanceEvent).where(MaintenanceEvent.id == first).values(action="changed")
        )
    c.execute(insert(MaintenanceEvent), row | dict(action="corrected", supersedes_id=first))
