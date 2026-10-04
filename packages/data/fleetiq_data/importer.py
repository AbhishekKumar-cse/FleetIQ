"""Restartable, authorized historical synthetic ingestion and atomic reference imports."""

import argparse
import json
import math
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid5

import pandas as pd
import sqlalchemy as sa
from fleetiq_domain.authorization import Principal, require
from fleetiq_domain.models.assets import AircraftType, Organization
from fleetiq_domain.models.components import Component, Installation
from fleetiq_domain.models.inventory import Inventory, SparePart, StockMovement
from fleetiq_domain.models.operations import (
    AuditEvent,
    EventOutbox,
    ImportBatch,
    Quarantine,
    User,
    enqueue_job,
)
from fleetiq_domain.models.telemetry import Sensor, Source, SourceEventReceipt, record_reading
from sqlalchemy.dialects.postgresql import insert as pg_insert

from fleetiq_data.quality.dedup import DuplicateConflict, content_hash, raw_json
from fleetiq_data.quality.profiles import load_profiles
from fleetiq_data.quality.time import classify_time, utc
from fleetiq_data.quality.values import essential_supported, validate_value
from fleetiq_data.quality.windows import transition_flags
from fleetiq_data.storage import store_raw

ROOT = Path(__file__).resolve().parents[3]
UNIT_FIELDS = {
    "temperature_c": "temperature_unit",
    "oil_pressure_kpa": "pressure_unit",
    "vibration_mm_s": "vibration_unit",
}


def parse_rows(path):
    path = Path(path)
    if path.suffix == ".parquet":
        return pd.read_parquet(path).to_dict("records")
    if path.suffix == ".csv":
        # Empty CSV cells are explicit nulls, not pandas' inferred NaN sentinels.
        return pd.read_csv(path, keep_default_na=False).replace({"": None}).to_dict("records")
    if path.suffix == ".json":
        data = json.loads(path.read_text())
        if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
            raise ValueError("JSON input must be an array of row objects")
        return data
    raise ValueError("Unsupported input format")


def _receipt(c, org, source, event_id, digest, now):
    keys = dict(organization_id=org, source_id=source, event_id=event_id)
    inserted = c.scalar(
        pg_insert(SourceEventReceipt)
        .values(**keys, payload_sha256=digest, received_at=now)
        .on_conflict_do_nothing()
        .returning(SourceEventReceipt.event_id)
    )
    if inserted is not None:
        return True
    existing = c.scalar(
        sa.select(SourceEventReceipt.payload_sha256).where(
            *[getattr(SourceEventReceipt, k) == v for k, v in keys.items()]
        )
    )
    if existing != digest:
        raise DuplicateConflict("Event ID has different content")
    return False


def _mapping(c, org, source):
    sensors = c.execute(
        sa.select(Sensor.__table__, Component.serial)
        .join(
            Component,
            sa.and_(
                Component.id == Sensor.component_id,
                Component.organization_id == Sensor.organization_id,
            ),
        )
        .where(Sensor.organization_id == org, Sensor.source_id == source)
    ).mappings()
    return {(r["serial"], r["channel_code"]): r for r in sensors}


def _telemetry(c, principal, source, row, profiles, mapping, *, now, streams):
    org = principal.organization_id
    try:
        if (
            row.get("source_kind") != "synthetic_engine"
            or row.get("schema_version") != "synthetic-engine-v1"
            or row.get("life_unit") != "operating_hours"
        ):
            raise ValueError("Source/schema/life-unit mismatch")
        at, recorded = utc(row["measured_at"]), utc(row["recorded_at"])
        classification = classify_time(at, recorded, now=now, mode="historical")
        if classification not in {"eligible", "historical_backfill"}:
            raise ValueError(classification)
        aircraft, installation = UUID(str(row["aircraft_id"])), UUID(str(row["installation_id"]))
        usage = float(row["operating_hours"])
        if not math.isfinite(usage) or usage < 0:
            raise ValueError("Invalid operating hours")
        serial = row["component_serial"]
        if row["engine_serial"] != serial:
            raise ValueError("Serial identity mismatch")
        sensor = mapping.get((serial, "temperature_c"))
        if sensor is None:
            raise ValueError("Unknown component/source mapping")
        installed = c.scalar(
            sa.select(Installation.id).where(
                Installation.organization_id == org,
                Installation.id == installation,
                Installation.aircraft_id == aircraft,
                Installation.component_id == sensor["component_id"],
                Installation.installed_at <= at,
                sa.or_(Installation.removed_at.is_(None), Installation.removed_at > at),
            )
        )
        if installed is None:
            raise ValueError("Reading outside matching installation")
        # Explicit IDs have priority. Native demo identity is installation + usage counter;
        # it deliberately excludes mutable measurement timestamp and measured values.
        event = (
            UUID(str(row["event_id"]))
            if row.get("event_id")
            else uuid5(source, f"{installation}:{usage:.12g}")
        )
    except (KeyError, ValueError, TypeError) as error:
        return "rejected", {"row": str(error)}, 0
    if not _receipt(c, org, source, event, content_hash(row), now):
        return "duplicate", {}, 0
    values = {
        channel: validate_value(
            row.get(channel),
            row.get(UNIT_FIELDS[channel]),
            profile,
            pressure_kind=row.get("pressure_kind", "absolute") if profile.pressure_kind else None,
        )
        for channel, profile in profiles.items()
    }
    problems = {k: list(v.reasons) for k, v in values.items() if v.quality != "valid"}
    previous = streams.get(str(installation), {})
    if previous and at < utc(previous["at"]):
        problems["time"] = ["historical_backfill"]
    elif previous:
        for channel, value in values.items():
            flags = transition_flags(
                previous.get("values", {}).get(channel),
                value.value,
                previous.get("usage"),
                usage,
                spike_delta=profiles[channel].spike_delta,
            )
            if flags:
                problems[channel] = list(problems.get(channel, [])) + list(flags)
    readings = 0
    for channel, value in values.items():
        sensor = mapping.get((serial, channel))
        if sensor is None or sensor["unit"] != value.canonical_unit:
            problems[channel] = ["Unknown sensor or canonical unit mismatch"]
            continue
        if value.value is None:
            continue
        reading = dict(
            organization_id=org,
            source_id=source,
            sensor_id=sensor["id"],
            installation_id=installation,
            observed_at=at,
            ingested_at=now,
            event_id=uuid5(event, channel),
            value=value.value,
            raw_value=json.dumps(raw_json(value.raw_value), ensure_ascii=False, allow_nan=False),
            canonical_unit=value.canonical_unit,
            raw_unit=value.raw_unit,
            quality="flagged" if channel in problems or "time" in problems else value.quality,
        )
        readings += int(record_reading(c, reading))
    if not readings:
        return "rejected", problems, 0
    if not essential_supported(values, profiles):
        problems["eligibility"] = ["essential_contract_failed"]
    if not previous or at >= utc(previous["at"]):
        streams[str(installation)] = dict(
            at=at.isoformat(), usage=usage, values={k: v.value for k, v in values.items()}
        )
    return "flagged" if problems else "accepted", problems, readings


def _audit(c, principal, batch, kind, summary):
    c.execute(
        sa.insert(EventOutbox).values(
            organization_id=principal.organization_id,
            scope_kind="organization",
            scope_id=principal.organization_id,
            kind="import." + kind,
            payload={
                "batch_id": str(batch),
                "counts": {k: v for k, v in summary.items() if k != "streams"},
            },
        )
    )
    c.execute(
        sa.insert(AuditEvent).values(
            organization_id=principal.organization_id,
            actor_id=principal.user_id,
            action="dataset:import",
            target_kind="import_batch",
            target_id=batch,
            scope_kind="organization",
            scope_id=principal.organization_id,
            versions={},
            reason="Validated " + kind + " batch commit",
        )
    )


def _atomic_reference(c, principal, kind, rows, checksum):
    """Lock/prevalidate all inputs before writing any configuration or physical stock."""
    require(c, principal, "asset:write" if kind == "configuration" else "inventory:write")
    staged, seen = [], set()
    for row in rows:
        model = AircraftType if kind == "configuration" else Inventory
        target = UUID(str(row["id"]))
        if target in seen:
            raise ValueError("Repeated reference target")
        seen.add(target)
        current = (
            c.execute(
                sa.select(model.__table__)
                .where(model.id == target, model.organization_id == principal.organization_id)
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if current is None:
            raise ValueError("Unknown reference target")
        if kind == "configuration":
            if set(row) != {"id", "configuration"} or not isinstance(row["configuration"], dict):
                raise ValueError("Configuration requires an id and object")
            content_hash(row["configuration"])
            staged.append((target, row["configuration"]))
        else:
            if set(row) != {"id", "version", "quantity_delta", "reason"}:
                raise ValueError("Inventory requires id/version/quantity_delta/reason")
            serialized = c.scalar(
                sa.select(SparePart.serialized).where(
                    SparePart.id == current["part_id"],
                    SparePart.organization_id == principal.organization_id,
                )
            )
            if serialized:
                raise ValueError("Serialized inventory requires a serial-specific ledger command")
            delta = Decimal(str(row["quantity_delta"]))
            if (
                not delta.is_finite()
                or delta == 0
                or delta.as_tuple().exponent < -6
                or type(row["version"]) is not int
                or not str(row["reason"]).strip()
                or row["version"] != current["version"]
                or current["on_hand"] + delta < current["reserved"] + current["quarantined"]
            ):
                raise ValueError("Invalid or stale inventory movement")
            staged.append((target, (current, delta, row["reason"])))
    for target, value in staged:
        if kind == "configuration":
            c.execute(
                sa.update(AircraftType)
                .where(
                    AircraftType.id == target,
                    AircraftType.organization_id == principal.organization_id,
                )
                .values(configuration=value)
            )
        else:
            current, delta, reason = value
            c.execute(
                sa.insert(StockMovement).values(
                    organization_id=principal.organization_id,
                    inventory_id=target,
                    quantity=delta,
                    actor_id=principal.user_id,
                    reason=reason,
                    idempotency_key=f"import:{checksum}:{target}",
                )
            )
            c.execute(
                sa.update(Inventory)
                .where(
                    Inventory.id == target, Inventory.organization_id == principal.organization_id
                )
                .values(on_hand=current["on_hand"] + delta, version=current["version"] + 1)
            )


def import_file(
    engine,
    principal,
    source_id,
    path,
    *,
    input_root,
    vault_root,
    batch_size=5000,
    kind="telemetry",
    before_commit=None,
):
    if not 1 <= batch_size <= 5000 or kind not in {"telemetry", "configuration", "inventory"}:
        raise ValueError("Invalid batch size or kind")
    # Authorize before copying/parsing an input or disclosing a source.
    with engine.begin() as c:
        require(c, principal, "dataset:import")
        source = (
            c.execute(
                sa.select(Source.__table__).where(
                    Source.id == source_id, Source.organization_id == principal.organization_id
                )
            )
            .mappings()
            .first()
        )
        if (
            source is None
            or source["source_kind"] != "synthetic_engine"
            or source["schema_version"] != "synthetic-engine-v1"
        ):
            raise ValueError("Source has no approved calendar ingestion mapping")
    stored, checksum = store_raw(path, input_root=input_root, vault_root=vault_root)
    rows = parse_rows(stored)
    profiles = load_profiles("synthetic_engine")
    keys = dict(
        organization_id=principal.organization_id, source_id=source_id, checksum=checksum, kind=kind
    )
    condition = [getattr(ImportBatch, k) == v for k, v in keys.items()]
    # Reference imports have one transaction, including the batch acknowledgment.
    if kind != "telemetry":
        with engine.begin() as c:
            require(c, principal, "dataset:import")
            c.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": f"{principal.organization_id}:{source_id}:{checksum}:{kind}"},
            )
            existing = (
                c.execute(sa.select(ImportBatch.__table__).where(*condition)).mappings().first()
            )
            if existing:
                return dict(existing["summary"])
            _atomic_reference(c, principal, kind, rows, checksum)
            summary = dict(
                processed=len(rows),
                accepted=len(rows),
                flagged=0,
                rejected=0,
                duplicate=0,
                readings=0,
                input_rows=len(rows),
            )
            batch = c.scalar(
                sa.insert(ImportBatch)
                .values(
                    **keys,
                    state="completed",
                    total_rows=len(rows),
                    accepted_rows=len(rows),
                    summary=summary,
                )
                .returning(ImportBatch.id)
            )
            _audit(c, principal, batch, kind, summary)
            if before_commit:
                before_commit(c, summary)
            return {k: v for k, v in summary.items() if k != "streams"}
    with engine.begin() as c:
        require(c, principal, "dataset:import")
        c.execute(
            pg_insert(ImportBatch)
            .values(
                **keys,
                state="validating",
                summary=dict(
                    processed=0,
                    accepted=0,
                    flagged=0,
                    rejected=0,
                    duplicate=0,
                    readings=0,
                    input_rows=len(rows),
                ),
            )
            .on_conflict_do_nothing()
        )
    while True:
        with engine.begin() as c:
            require(c, principal, "dataset:import")
            batch = (
                c.execute(sa.select(ImportBatch.__table__).where(*condition).with_for_update())
                .mappings()
                .one()
            )
            summary = dict(batch["summary"])
            if batch["state"] == "completed":
                return {k: v for k, v in summary.items() if k != "streams"}
            start, now = summary["processed"], datetime.now(UTC)
            stop = min(len(rows), start + batch_size)
            mapping = _mapping(c, principal.organization_id, source_id)
            for number in range(start, stop):
                row = rows[number]
                status, problems, readings = _telemetry(
                    c,
                    principal,
                    source_id,
                    row,
                    profiles,
                    mapping,
                    now=now,
                    streams=summary.setdefault("streams", {}),
                )
                summary[status] += 1
                summary["readings"] += readings
                if problems:
                    c.execute(
                        sa.insert(Quarantine).values(
                            organization_id=principal.organization_id,
                            batch_id=batch["id"],
                            row_number=number,
                            raw_row=raw_json(row),
                            reason=json.dumps(problems, sort_keys=True),
                        )
                    )
            summary["processed"] = stop
            c.execute(
                sa.update(ImportBatch)
                .where(
                    ImportBatch.id == batch["id"],
                    ImportBatch.organization_id == principal.organization_id,
                )
                .values(
                    state="completed" if stop == len(rows) else "validating",
                    total_rows=stop,
                    accepted_rows=summary["accepted"] + summary["flagged"] + summary["duplicate"],
                    quarantined_rows=summary["rejected"],
                    summary=summary,
                )
            )
            _audit(c, principal, batch["id"], "progress", summary)
            enqueue_job(
                c,
                dict(
                    organization_id=principal.organization_id,
                    owner_id=principal.user_id,
                    kind="ingestion.summary",
                    input_hash=content_hash({"batch": batch["id"], "stop": stop}),
                    input={"batch_id": str(batch["id"]), "through_row": stop},
                    idempotency_key=f"{batch['id']}:{stop}",
                ),
                actor_id=principal.user_id,
                reason="Committed validated import chunk",
            )
            if before_commit:
                before_commit(c, summary)
            if stop == len(rows):
                return {k: v for k, v in summary.items() if k != "streams"}


def main():
    from fleetiq_api.settings import Settings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--source", choices=["synthetic"], required=True)
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument("--organization", default="FLEETIQ-DEMO")
    parser.add_argument("--subject", default="demo-admin")
    parser.add_argument(
        "--kind", choices=["telemetry", "configuration", "inventory"], default="telemetry"
    )
    args = parser.parse_args()
    settings = Settings()
    engine = sa.create_engine(settings.database_url.get_secret_value(), hide_parameters=True)
    try:
        with engine.connect() as c:
            row = (
                c.execute(
                    sa.select(User.id, User.organization_id)
                    .join(Organization, Organization.id == User.organization_id)
                    .where(
                        Organization.code == args.organization,
                        User.subject == args.subject,
                        User.active,
                    )
                )
                .mappings()
                .one()
            )
            source = c.scalar(
                sa.select(Source.id).where(
                    Source.organization_id == row["organization_id"],
                    Source.code == "DEMO-SYNTHETIC",
                )
            )
        path = args.input / "sensor_observations.parquet" if args.input.is_dir() else args.input
        summary = import_file(
            engine,
            Principal(row["organization_id"], row["id"]),
            source,
            path,
            input_root=ROOT / "data",
            vault_root=ROOT / "data/raw/imports",
            batch_size=args.batch_size,
            kind=args.kind,
        )
        print(json.dumps(summary, sort_keys=True))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
