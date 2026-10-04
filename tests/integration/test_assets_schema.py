from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fleetiq_domain.db import Base
from fleetiq_domain.models.assets import Aircraft, AircraftType, Fleet, Organization, Site
from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@pytest.fixture
def assets(isolated_database):
    url, _ = isolated_database
    engine = create_engine(url)
    tenants = []
    try:
        with engine.begin() as connection:
            for code in ("DEMO-A", "DEMO-B"):
                ids = {name: uuid4() for name in ("organization", "site", "fleet", "type")}
                connection.execute(
                    insert(Organization), dict(id=ids["organization"], code=code, name=code)
                )
                for model, field in ((Site, "site"), (Fleet, "fleet"), (AircraftType, "type")):
                    row = dict(id=ids[field], organization_id=ids["organization"], code="DEMO-CODE")
                    if model != AircraftType:
                        row["name"] = "Fictional fixture"
                    connection.execute(insert(model), row)
                tenants.append(ids)
        yield engine, tenants
    finally:
        engine.dispose()


def aircraft_row(ids, **changes):
    return (
        dict(
            organization_id=ids["organization"],
            tail_label="DEMO-001",
            type_id=ids["type"],
            site_id=ids["site"],
            fleet_id=ids["fleet"],
        )
        | changes
    )


def test_valid_identity_defaults_and_same_tail_in_different_organizations(assets):
    engine, tenants = assets
    with Session(engine) as session:
        for ids in tenants:
            session.add(Aircraft(**aircraft_row(ids)))
        session.commit()
        rows = session.scalars(select(Aircraft)).all()
        assert len(rows) == 2 and rows[0].id != rows[1].id
        assert all(row.status_version == 0 and row.created_at.tzinfo is not None for row in rows)
        assert all(row.id != row.type_id for row in rows)


def test_duplicate_scoped_tail_and_code_rejected(assets):
    engine, (ids, _) = assets
    with engine.begin() as connection:
        connection.execute(insert(Aircraft), aircraft_row(ids))
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(insert(Aircraft), aircraft_row(ids))
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                insert(Site),
                dict(organization_id=ids["organization"], code="DEMO-CODE", name="duplicate"),
            )


@pytest.mark.parametrize("field", ["type", "site", "fleet"])
def test_cross_organization_links_rejected(assets, field):
    engine, (first, second) = assets
    with engine.begin() as connection:
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                insert(Aircraft), aircraft_row(first, **{f"{field}_id": second[field]})
            )


@pytest.mark.parametrize(
    "changes", [{"type_id": None}, {"type_id": uuid4()}, {"status_version": -1}]
)
def test_missing_type_and_invalid_projection_version_rejected(assets, changes):
    engine, (ids, _) = assets
    with engine.begin() as connection:
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(insert(Aircraft), aircraft_row(ids, **changes))


def test_migration_matches_orm_and_runtime_roles_are_read_only(assets):
    engine, _ = assets
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        for role in ("fleetiq_app", "fleetiq_worker"):
            assert connection.scalar(
                text("SELECT has_table_privilege(:role, 'aircraft', 'SELECT')"), {"role": role}
            )
            assert not connection.scalar(
                text("SELECT has_table_privilege(:role, 'aircraft', 'INSERT')"), {"role": role}
            )
