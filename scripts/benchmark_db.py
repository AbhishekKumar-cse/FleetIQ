"""Measure real EXPLAIN ANALYZE plans in a guarded disposable database, never live storage."""

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fleetiq_api.settings import Settings
from fleetiq_data.manifest import sha256
from fleetiq_data.seed import demo_id, seed_database
from fleetiq_data.verify_db import verify_database
from fleetiq_domain.models.telemetry import SensorReading, SourceEventReceipt
from fleetiq_domain.testing import temporary_database
from sqlalchemy import create_engine, insert, text


def measure_baseline(connection, *, rows=20000):
    if not 1000 <= rows <= 100000:
        raise ValueError("bounded synthetic benchmark load required")
    org = demo_id("organization")
    target = (
        connection.execute(
            text(
                "SELECT s.id,s.source_id,s.unit,i.id AS installation_id,i.installed_at "
                "FROM sensor s JOIN installation i ON i.organization_id=s.organization_id AND i.component_id=s.component_id "
                "WHERE s.organization_id=:org AND i.removed_at IS NULL ORDER BY s.id LIMIT 1"
            ),
            {"org": org},
        )
        .mappings()
        .one()
    )
    readings = []
    receipts = []
    for index in range(rows):
        when = target["installed_at"] + timedelta(seconds=index + 1)
        event = demo_id("benchmark-event", str(index))
        row = dict(
            organization_id=org,
            sensor_id=target["id"],
            source_id=target["source_id"],
            event_id=event,
            observed_at=when,
            ingested_at=when,
            installation_id=target["installation_id"],
            value=1 + (index % 7) / 10,
            raw_value=str(1 + (index % 7) / 10),
            canonical_unit=target["unit"],
            raw_unit=target["unit"],
            quality="valid",
        )
        digest = sha256(
            json.dumps(
                {k: v for k, v in row.items() if k != "ingested_at"}, sort_keys=True, default=str
            ).encode()
        )
        readings.append(row)
        receipts.append(
            dict(
                organization_id=org,
                source_id=target["source_id"],
                event_id=event,
                payload_sha256=digest,
                received_at=when,
            )
        )
    connection.execute(insert(SourceEventReceipt), receipts)
    connection.execute(insert(SensorReading), readings)
    connection.exec_driver_sql("ANALYZE sensor_reading")
    params = {
        "org": org,
        "sensor": target["id"],
        "cutoff": readings[-1]["observed_at"],
        "window_start": readings[-1]["observed_at"] - timedelta(seconds=120),
    }
    queries = {
        "sensor_recent_window": "SELECT value,observed_at FROM sensor_reading WHERE organization_id=:org "
        "AND sensor_id=:sensor AND observed_at>=:window_start AND observed_at<=:cutoff "
        "ORDER BY observed_at DESC LIMIT 120",
        "aircraft_components": "SELECT a.id,a.tail_label,c.serial FROM aircraft a JOIN installation i "
        "ON i.aircraft_id=a.id AND i.organization_id=a.organization_id JOIN component c "
        "ON c.id=i.component_id AND c.organization_id=i.organization_id "
        "WHERE a.organization_id=:org AND i.removed_at IS NULL",
        "inventory": "SELECT part_id,site_id,on_hand-quarantined-reserved AS available FROM inventory "
        "WHERE organization_id=:org AND condition='serviceable'",
        "fleet_counts": "SELECT status,count(*) FROM (SELECT DISTINCT ON (aircraft_id) aircraft_id,status "
        "FROM aircraft_status_event WHERE organization_id=:org ORDER BY aircraft_id,version DESC) latest GROUP BY status",
    }
    measured = {}
    for name, query in queries.items():
        plan = connection.scalar(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query), params)[
            0
        ]
        measured[name] = {
            "planning_ms": plan["Planning Time"],
            "execution_ms": plan["Execution Time"],
            "plan": plan["Plan"],
        }
    all_indexes = list(
        connection.scalars(text("SELECT indexname FROM pg_indexes WHERE schemaname='public'"))
    )
    if not any(
        "ix_reading_org_sensor_time" in str(value)
        for value in measured["sensor_recent_window"]["plan"].values()
    ):
        # Inspect the complete nested Timescale plan, not just the top-level node.
        if "ix_reading_org_sensor_time" not in json.dumps(measured["sensor_recent_window"]["plan"]):
            raise ValueError("recent-window query did not demonstrate the required index")
    return {
        "measured_at": datetime.now(UTC).isoformat(),
        "verification_after_load": verify_database(connection, org),
        "database": "guarded disposable UUID database",
        "load": {
            "kind": "purpose-built synthetic query fixture",
            "sensor_readings": rows,
            "operational_telemetry_imported": False,
        },
        "queries": measured,
        "public_indexes": all_indexes,
        "limits": "Single local run; not production capacity, model accuracy or availability improvement.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/synthetic"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings()
    output = args.output.resolve()
    if not output.is_relative_to(settings.report_root) or args.output.is_symlink():
        parser.error("benchmark report must stay in the ignored report root")
    with temporary_database(settings) as (url, _):
        engine = create_engine(url, hide_parameters=True)
        try:
            with engine.begin() as c:
                seeded = seed_database(c, args.input, demo_only=True)
                result = measure_baseline(c)
                result["seed"] = seeded
        finally:
            engine.dispose()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, default=str) + "\n")
    print(
        json.dumps({name: row["execution_ms"] for name, row in result["queries"].items()}, indent=2)
    )


if __name__ == "__main__":
    main()
