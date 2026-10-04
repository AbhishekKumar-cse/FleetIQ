"""HTTP telemetry shares the file importer's identity, mapping, units and quality logic."""

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from fleetiq_domain.authorization import require
from fleetiq_domain.models.ingestion import IngestionReceipt, SourceWatermark
from fleetiq_domain.models.operations import EventOutbox
from fleetiq_domain.models.telemetry import Source
from sqlalchemy.dialects.postgresql import insert as pg_insert

from fleetiq_data.backfill import queue_backfill
from fleetiq_data.importer import UNIT_FIELDS, _mapping, _telemetry
from fleetiq_data.quality.dedup import DuplicateConflict, content_hash
from fleetiq_data.quality.profiles import load_profiles
from fleetiq_data.quality.time import classify_time, utc
from fleetiq_data.quality.values import validate_value


def approved_source(c, principal, source):
    require(c, principal, "dataset:import")
    row = (
        c.execute(
            sa.select(Source.__table__).where(
                Source.id == source, Source.organization_id == principal.organization_id
            )
        )
        .mappings()
        .first()
    )
    if (
        not row
        or row["source_kind"] != "synthetic_engine"
        or row["schema_version"] != "synthetic-engine-v1"
    ):
        raise ValueError("Source has no approved calendar ingestion contract")


def receipt(c, principal, source, key, route, digest):
    c.execute(
        sa.text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"http:{principal.organization_id}:{source}:{route}:{key}"},
    )
    old = (
        c.execute(
            sa.select(IngestionReceipt.__table__).where(
                IngestionReceipt.organization_id == principal.organization_id,
                IngestionReceipt.source_id == source,
                IngestionReceipt.request_key == key,
                IngestionReceipt.route == route,
            )
        )
        .mappings()
        .first()
    )
    if old and old["input_hash"] != digest:
        raise DuplicateConflict("Idempotency key has different content")
    return old["response"] if old else None


def persist_receipt(c, principal, source, key, route, digest, response):
    c.execute(
        sa.insert(IngestionReceipt).values(
            organization_id=principal.organization_id,
            source_id=source,
            request_key=key,
            route=route,
            input_hash=digest,
            response=response,
        )
    )


def ingest_rows(c, principal, source, rows, *, mode="replay", now=None):
    approved_source(c, principal, source)
    if mode not in {"historical", "replay"} or not rows or len(rows) * 3 > 1000:
        raise ValueError("Batch requires 1–333 three-channel observations (maximum 1000 readings)")
    profiles = load_profiles("synthetic_engine")
    mapping = _mapping(c, principal.organization_id, source)
    now = now or datetime.now(UTC)
    result = dict(
        accepted=0,
        flagged=0,
        duplicate=0,
        readings=0,
        observations=len(rows),
        historical=mode == "historical",
    )
    streams = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Observation object required")
        for channel, profile in profiles.items():
            value = validate_value(
                row.get(channel),
                row.get(UNIT_FIELDS[channel]),
                profile,
                pressure_kind=row.get("pressure_kind", "absolute")
                if profile.pressure_kind
                else None,
            )
            if value.quality == "invalid":
                raise ValueError("Invalid value or incompatible unit")
        sensor = mapping.get((row.get("component_serial"), "temperature_c"))
        if not sensor:
            raise ValueError("Source component mapping required")
        component = sensor["component_id"]
        keys = dict(
            organization_id=principal.organization_id, source_id=source, component_id=component
        )
        c.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"watermark:{principal.organization_id}:{source}:{component}"},
        )
        latest = c.scalar(
            sa.select(SourceWatermark.latest_at).where(
                *[getattr(SourceWatermark, k) == v for k, v in keys.items()]
            )
        )
        at = utc(row["measured_at"])
        timing = classify_time(at, utc(row["recorded_at"]), now=now, latest=latest, mode=mode)
        if timing in {"future", "invalid_recording_order"}:
            raise ValueError("Invalid observation chronology")
        status, problems, count = _telemetry(
            c, principal, source, row, profiles, mapping, now=now, streams=streams
        )
        if status == "rejected":
            raise ValueError("Observation rejected by ingestion contract")
        result[status] += 1
        result["readings"] += count
        if status != "duplicate":
            historical = (
                mode == "historical"
                or timing == "late_backfill_required"
                or now - at > timedelta(seconds=120)
            )
            queue_backfill(
                c,
                principal,
                component_id=component,
                as_of=at,
                source_cutoff=now,
                input_hash=content_hash(row),
                historical=historical,
            )
            if latest is None or at > latest:
                c.execute(
                    pg_insert(SourceWatermark)
                    .values(**keys, latest_at=at)
                    .on_conflict_do_update(
                        index_elements=["organization_id", "source_id", "component_id"],
                        set_={"latest_at": at},
                    )
                )
    c.execute(
        sa.insert(EventOutbox).values(
            organization_id=principal.organization_id,
            scope_kind="organization",
            scope_id=principal.organization_id,
            kind="telemetry.committed",
            payload=result,
        )
    )
    return result
