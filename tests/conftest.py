"""Disposable integration databases are named and guarded before creation/cleanup."""

import pytest
from sqlalchemy import create_engine


@pytest.fixture
def domain_connection(isolated_database):
    from datetime import UTC, datetime

    from fleetiq_domain.models.assets import Aircraft, AircraftType, Fleet, Organization, Site
    from fleetiq_domain.models.components import Component, Installation
    from fleetiq_domain.models.inventory import SparePart
    from fleetiq_domain.models.operations import User
    from sqlalchemy import insert

    engine = create_engine(isolated_database[0])
    try:
        with engine.begin() as connection:
            ids = {}
            ids["organization"] = connection.scalar(
                insert(Organization).values(code="DEMO", name="Demo").returning(Organization.id)
            )
            ids["user"] = connection.scalar(
                insert(User)
                .values(
                    organization_id=ids["organization"],
                    subject="fixture",
                    display_name="Fixture",
                    password_hash="$argon2id$fixture",
                )
                .returning(User.id)
            )
            for key, model in (("site", Site), ("fleet", Fleet), ("type", AircraftType)):
                values = dict(organization_id=ids["organization"], code="DEMO")
                if model != AircraftType:
                    values["name"] = "Demo"
                ids[key] = connection.scalar(insert(model).values(**values).returning(model.id))
            ids["aircraft"] = connection.scalar(
                insert(Aircraft)
                .values(
                    organization_id=ids["organization"],
                    tail_label="DEMO",
                    type_id=ids["type"],
                    site_id=ids["site"],
                    fleet_id=ids["fleet"],
                )
                .returning(Aircraft.id)
            )
            ids["part"] = connection.scalar(
                insert(SparePart)
                .values(
                    organization_id=ids["organization"], code="DEMO", kind="engine", serialized=True
                )
                .returning(SparePart.id)
            )
            ids["component"] = connection.scalar(
                insert(Component)
                .values(
                    organization_id=ids["organization"],
                    serial="DEMO",
                    kind="engine",
                    part_id=ids["part"],
                )
                .returning(Component.id)
            )
            ids["installation"] = connection.scalar(
                insert(Installation)
                .values(
                    organization_id=ids["organization"],
                    aircraft_id=ids["aircraft"],
                    component_id=ids["component"],
                    position="engine",
                    installed_at=datetime(2026, 1, 1, tzinfo=UTC),
                )
                .returning(Installation.id)
            )
            yield connection, ids
    finally:
        engine.dispose()


@pytest.fixture
def isolated_database():
    from fleetiq_domain.testing import temporary_database

    with temporary_database() as database:
        yield database
