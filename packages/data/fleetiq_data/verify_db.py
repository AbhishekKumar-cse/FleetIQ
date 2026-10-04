"""Read-only verification of canonical relationships and seeded reference coverage."""

import json

from fleetiq_api.settings import Settings
from sqlalchemy import create_engine, text


def verify_database(connection, organization_id=None):
    if organization_id is None:
        organization_id = connection.scalar(
            text("SELECT id FROM organization WHERE code='FLEETIQ-DEMO'")
        )
    if organization_id is None:
        raise ValueError("fictional demo organization is not seeded")
    params = {"org": organization_id}
    marker = (
        connection.execute(
            text(
                "SELECT checksum,summary FROM import_batch WHERE organization_id=:org "
                "AND kind='reference_seed' AND state='completed'"
            ).bindparams(**params)
        )
        .mappings()
        .one()
    )
    counts = {
        table: connection.scalar(
            text(f"SELECT count(*) FROM {table} WHERE organization_id=:org"), params
        )
        for table in (
            "aircraft",
            "component",
            "installation",
            "sensor",
            "sensor_reading",
            "inventory",
            "procedure_revision",
        )
    }
    if any(counts[table] < expected for table, expected in marker["summary"].items()):
        raise ValueError("seeded reference coverage is incomplete")
    invalid = connection.scalar(
        text(
            "SELECT count(*) FROM sensor_reading r JOIN sensor s ON "
            "s.id=r.sensor_id AND s.organization_id=r.organization_id JOIN installation i ON "
            "i.id=r.installation_id AND i.organization_id=r.organization_id WHERE r.organization_id=:org "
            "AND (s.component_id<>i.component_id OR r.observed_at<i.installed_at OR "
            "(i.removed_at IS NOT NULL AND r.observed_at>=i.removed_at))"
        ),
        params,
    )
    pending = connection.scalar(
        text(
            "SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON "
            "n.oid=c.connamespace WHERE n.nspname='public' AND NOT c.convalidated"
        )
    )
    stock = connection.scalar(
        text(
            "SELECT count(*) FROM inventory WHERE organization_id=:org AND "
            "(on_hand<0 OR reserved<0 OR quarantined<0 OR reserved>on_hand-quarantined)"
        ),
        params,
    )
    if invalid or pending or stock:
        raise ValueError("database relationship/constraint/stock verification failed")
    return {
        "organization_id": str(organization_id),
        "manifest_hash": marker["checksum"],
        "counts": counts,
        "invalid_reading_installations": invalid,
        "unvalidated_constraints": pending,
        "invalid_stock_balances": stock,
        "head": connection.scalar(text("SELECT version_num FROM alembic_version")),
    }


def main():
    engine = create_engine(
        Settings().migration_database_url.get_secret_value(), hide_parameters=True
    )
    try:
        with engine.connect() as c:
            c.exec_driver_sql("SET TRANSACTION READ ONLY")
            print(json.dumps(verify_database(c), indent=2))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
