import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration


def test_changed_timestamp_cannot_bypass_event_receipt(domain_connection):
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from fleetiq_domain.models.telemetry import Sensor, Source, record_reading
    from sqlalchemy import insert

    c, ids = domain_connection
    org = ids["organization"]
    source = c.scalar(
        insert(Source)
        .values(
            organization_id=org, code="DEMO", source_kind="synthetic_engine", schema_version="v1"
        )
        .returning(Source.id)
    )
    sensor = c.scalar(
        insert(Sensor)
        .values(
            organization_id=org,
            component_id=ids["component"],
            source_id=source,
            channel_code="temperature",
            unit="degC",
        )
        .returning(Sensor.id)
    )
    now = datetime(2026, 1, 2, tzinfo=UTC)
    row = dict(
        organization_id=org,
        sensor_id=sensor,
        source_id=source,
        event_id=uuid4(),
        installation_id=ids["installation"],
        observed_at=now,
        ingested_at=now,
        value=80,
        raw_value="80",
        canonical_unit="degC",
        raw_unit="degC",
        quality="valid",
    )
    assert record_reading(c, row)
    assert not record_reading(c, row)
    with pytest.raises(ValueError):
        record_reading(c, row | dict(observed_at=now + timedelta(hours=1)))


def test_hypertable_and_global_receipt_keys(isolated_database):
    engine = create_engine(isolated_database[0])
    try:
        with engine.connect() as c:
            assert (
                c.scalar(
                    text(
                        "SELECT count(*) FROM timescaledb_information.hypertables WHERE hypertable_name='sensor_reading'"
                    )
                )
                == 1
            )
            assert (
                c.scalar(
                    text(
                        "SELECT count(*) FROM timescaledb_information.hypertables WHERE hypertable_name='source_event_receipt'"
                    )
                )
                == 0
            )
            keys = c.scalar(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='source_event_receipt'::regclass AND contype='p'"
                )
            )
            assert all(key in keys for key in ("organization_id", "source_id", "event_id"))
    finally:
        engine.dispose()
