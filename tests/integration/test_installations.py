from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fleetiq_domain.models.assets import Aircraft, AircraftType, Fleet, Organization, Site
from fleetiq_domain.models.components import Component, Engine, Installation
from sqlalchemy import create_engine, insert
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration


def test_replacement_intervals_and_engine_identity(isolated_database):
    engine = create_engine(isolated_database[0])
    try:
        with engine.begin() as c:
            org, site, fleet, kind, aircraft, one, two = [uuid4() for _ in range(7)]
            c.execute(insert(Organization), dict(id=org, code="DEMO", name="Demo"))
            for model, key in ((Site, site), (Fleet, fleet)):
                c.execute(
                    insert(model), dict(id=key, organization_id=org, code="DEMO", name="Demo")
                )
            c.execute(insert(AircraftType), dict(id=kind, organization_id=org, code="DEMO"))
            c.execute(
                insert(Aircraft),
                dict(
                    id=aircraft,
                    organization_id=org,
                    tail_label="DEMO",
                    type_id=kind,
                    site_id=site,
                    fleet_id=fleet,
                ),
            )
            for key in (one, two):
                c.execute(
                    insert(Component),
                    dict(id=key, organization_id=org, serial=str(key), kind="engine"),
                )
                c.execute(
                    insert(Engine), dict(component_id=key, organization_id=org, engine_type="DEMO")
                )
            now = datetime(2026, 1, 1, tzinfo=UTC)
            row = dict(
                organization_id=org,
                aircraft_id=aircraft,
                component_id=one,
                position="engine",
                installed_at=now,
                removed_at=now + timedelta(hours=1),
            )
            c.execute(insert(Installation), row)
            c.execute(
                insert(Installation),
                row
                | dict(component_id=two, installed_at=now + timedelta(hours=1), removed_at=None),
            )
            for change in ({"component_id": two}, {"position": "other"}, {"removed_at": now}):
                with pytest.raises(IntegrityError), c.begin_nested():
                    c.execute(insert(Installation), row | change)
    finally:
        engine.dispose()
