"""Native readings and ordinary globally unique source receipts."""

import hashlib
import json

from sqlalchemy import ForeignKeyConstraint, Index, insert, select

from fleetiq_domain.models.schema import entity

Source = entity(
    "source",
    {"code": "text:required", "source_kind": "text:required", "schema_version": "text:required"},
    unique=(("code",),),
    checks=("source_kind IN ('nasa_cmapss','synthetic_engine')",),
)
Sensor = entity(
    "sensor",
    {
        "component_id": "uuid:required",
        "source_id": "uuid:required",
        "channel_code": "text:required",
        "unit": "text:required",
        "essential": "bool:required:true",
    },
    refs={"component_id": "component", "source_id": "source"},
    unique=(("component_id", "channel_code", "source_id"), ("id", "source_id")),
)
SourceEventReceipt = entity(
    "source_event_receipt",
    {
        "source_id": "uuid:required",
        "event_id": "uuid:required",
        "payload_sha256": "text:required",
        "received_at": "time:required",
    },
    refs={"source_id": "source"},
    primary=("organization_id", "source_id", "event_id"),
    checks=("payload_sha256 ~ '^[0-9a-f]{64}$'",),
)
SensorReading = entity(
    "sensor_reading",
    {
        "sensor_id": "uuid:required",
        "source_id": "uuid:required",
        "event_id": "uuid:required",
        "installation_id": "uuid:required",
        "observed_at": "time:required",
        "ingested_at": "time:required",
        "value": "float:optional",
        "raw_value": "text:optional",
        "canonical_unit": "text:required",
        "raw_unit": "text:required",
        "quality": "text:required",
    },
    refs={"sensor_id": "sensor", "source_id": "source", "installation_id": "installation"},
    primary=("sensor_id", "observed_at", "event_id"),
    checks=(
        "quality IN ('valid','flagged','missing','invalid')",
        "value IS NULL OR (value > '-Infinity'::float8 AND value < 'Infinity'::float8)",
        "quality <> 'valid' OR value IS NOT NULL",
    ),
)
SensorReading.__table__.append_constraint(
    ForeignKeyConstraint(
        ["organization_id", "source_id", "event_id"],
        [
            "source_event_receipt.organization_id",
            "source_event_receipt.source_id",
            "source_event_receipt.event_id",
        ],
        name="fk_reading_receipt",
    )
)
SensorReading.__table__.append_constraint(
    ForeignKeyConstraint(
        ["organization_id", "sensor_id", "source_id"],
        ["sensor.organization_id", "sensor.id", "sensor.source_id"],
        name="fk_reading_sensor_source",
    )
)
Index(
    "ix_reading_org_sensor_time",
    SensorReading.organization_id,
    SensorReading.sensor_id,
    SensorReading.observed_at,
)
EXTRA = [
    "SELECT create_hypertable('sensor_reading', by_range('observed_at'), create_default_indexes => false)",
    "CREATE INDEX ix_reading_org_sensor_time ON sensor_reading (organization_id,sensor_id,observed_at)",
    """CREATE FUNCTION fleetiq_validate_reading() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
       IF NOT EXISTS(SELECT 1 FROM installation i JOIN sensor s ON s.component_id=i.component_id AND s.organization_id=i.organization_id
         WHERE i.id=NEW.installation_id AND s.id=NEW.sensor_id AND i.organization_id=NEW.organization_id
         AND NEW.observed_at >= i.installed_at AND (i.removed_at IS NULL OR NEW.observed_at < i.removed_at))
       THEN RAISE EXCEPTION 'reading outside installed component interval'; END IF; RETURN NEW; END $$""",
    "CREATE TRIGGER validate_reading BEFORE INSERT OR UPDATE ON sensor_reading FOR EACH ROW EXECUTE FUNCTION fleetiq_validate_reading()",
]


def record_reading(connection, row):
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    digest = hashlib.sha256(
        json.dumps(
            {k: v for k, v in row.items() if k != "ingested_at"}, default=str, sort_keys=True
        ).encode()
    ).hexdigest()
    keys = {k: row[k] for k in ("organization_id", "source_id", "event_id")}
    stmt = (
        pg_insert(SourceEventReceipt)
        .values(**keys, payload_sha256=digest, received_at=row["ingested_at"])
        .on_conflict_do_nothing()
        .returning(SourceEventReceipt.event_id)
    )
    if connection.scalar(stmt) is None:
        condition = [getattr(SourceEventReceipt, key) == value for key, value in keys.items()]
        existing = connection.scalar(
            select(SourceEventReceipt.payload_sha256).where(*condition).with_for_update()
        )
        if existing != digest:
            raise ValueError("event ID reused with changed payload")
        return False
    connection.execute(insert(SensorReading), row)
    return True
