"""Non-destructive, manifest-verified fictional asset/reference seeding; no evaluator input."""

import argparse
import json
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import pandas as pd
from fleetiq_api.settings import Settings
from fleetiq_domain.models.assets import Aircraft, AircraftType, Fleet, Organization, Site
from fleetiq_domain.models.components import Component, Engine, Installation
from fleetiq_domain.models.fleet import (
    AircraftSnapshot,
    AircraftStatusEvent,
    FleetSnapshot,
    ResourceCalendar,
)
from fleetiq_domain.models.inventory import Inventory, PartCompatibility, SparePart
from fleetiq_domain.models.operations import ImportBatch, Role, RoleAssignment, User
from fleetiq_domain.models.predictions import PolicyVersion
from fleetiq_domain.models.telemetry import Sensor, Source
from fleetiq_domain.models.work import ProcedureRevision
from pwdlib import PasswordHash
from sqlalchemy import create_engine, select, update
from sqlalchemy.dialects.postgresql import insert

from fleetiq_data.fetch import ROOT
from fleetiq_data.manifest import canonical_json, sha256

REFERENCE_TABLES = (
    "aircraft",
    "components",
    "installations",
    "parts",
    "task_templates",
    "part_compatibility",
    "workers",
    "bays",
    "stock_movements",
)


def demo_id(kind, value="demo"):
    return uuid5(NAMESPACE_URL, f"fleetiq:reference-seed:{kind}:{value}")


def load_reference(input_dir):
    input_dir = Path(input_dir).resolve()
    manifest = json.loads((input_dir / "manifest.json").read_text())
    digest = manifest.pop("content_sha256")
    if sha256(canonical_json(manifest)) != digest:
        raise ValueError("manifest checksum mismatch")
    manifest["content_sha256"] = digest
    if (
        manifest["manifest_version"] != "dataset-manifest-v1"
        or manifest["track"] != "synthetic_engine_demo"
    ):
        raise ValueError("only the manifest-verified fictional demo track may be seeded")
    cutoff = datetime.fromisoformat(manifest["cutoff"])
    if cutoff.tzinfo is None:
        raise ValueError("source cutoff must be aware")
    tables = {}
    for name in REFERENCE_TABLES:
        relative = f"observed/{name}.parquet"
        path = input_dir / relative
        if (
            path.is_symlink()
            or path.parent.is_symlink()
            or sha256(path.read_bytes()) != manifest["files"][relative]
        ):
            raise ValueError("reference checksum or access-boundary mismatch")
        tables[name] = pd.read_parquet(path)
    for name in ("aircraft", "components", "installations"):
        if tables[name].empty or not tables[name].fictional.all():
            raise ValueError("nonfictional or empty asset input rejected")
    if not tables["parts"].part_code.str.startswith("DEMO-").all():
        raise ValueError("part references must belong to the fictional demo")
    return manifest, tables, cutoff


def ensure(connection, model, row, *, mutable=()):
    table = model.__table__
    keys = ("component_id",) if model is Engine else ("id",)
    connection.execute(
        insert(model).values(**row).on_conflict_do_nothing(index_elements=list(keys))
    )
    existing = (
        connection.execute(select(table).where(*(table.c[k] == row[k] for k in keys)))
        .mappings()
        .one()
    )
    if any(existing[key] != value for key, value in row.items() if key not in mutable):
        raise ValueError(f"conflicting existing {table.name}; nothing is overwritten")
    return row[keys[0]]


def seed_database(connection, input_dir, *, demo_only=False, password_hash=None):
    if not demo_only:
        raise ValueError("explicit demo-only mode required")
    manifest, tables, cutoff = load_reference(input_dir)
    org = demo_id("organization")
    existing = connection.scalar(
        select(ImportBatch.checksum).where(
            ImportBatch.organization_id == org,
            ImportBatch.kind == "reference_seed",
            ImportBatch.state == "completed",
        )
    )
    if existing:
        if existing != manifest["content_sha256"]:
            raise ValueError("different demo manifest already seeded; use an isolated database")
        from fleetiq_data.verify_db import verify_database

        return verify_database(connection, org)
    # One transaction/savepoint covers every relationship and the completion marker.
    with connection.begin_nested():
        ensure(
            connection,
            Organization,
            dict(id=org, code="FLEETIQ-DEMO", name="FleetIQ fictional demo"),
        )
        user = demo_id("user")
        ensure(
            connection,
            User,
            dict(
                id=user,
                organization_id=org,
                subject="demo-admin",
                display_name="Local demo administrator",
                password_hash=password_hash or "$argon2id$disabled",
                active=password_hash is not None,
            ),
        )
        role = demo_id("role")
        ensure(
            connection,
            Role,
            dict(id=role, organization_id=org, code="admin", permissions=["users:manage"]),
        )
        ensure(
            connection,
            RoleAssignment,
            dict(
                id=demo_id("role-assignment"),
                organization_id=org,
                user_id=user,
                role_id=role,
                scope_kind="organization",
            ),
        )

        def refs(model, kind, codes):
            for code in sorted(set(codes)):
                row = dict(id=demo_id(kind, code), organization_id=org, code=code)
                if model is not AircraftType:
                    row["name"] = code
                ensure(connection, model, row)

        aircraft = tables["aircraft"]
        refs(Site, "site", aircraft.site_code)
        refs(Fleet, "fleet", aircraft.fleet_code)
        refs(AircraftType, "type", aircraft.type_code)
        for row in tables["parts"].to_dict("records"):
            ensure(
                connection,
                SparePart,
                dict(
                    id=demo_id("part", row["part_code"]),
                    organization_id=org,
                    code=row["part_code"],
                    kind="engine",
                    pack_size=1,
                    serialized=True,
                ),
            )
        for row in aircraft.to_dict("records"):
            ensure(
                connection,
                Aircraft,
                dict(
                    id=UUID(row["aircraft_id"]),
                    organization_id=org,
                    tail_label=row["tail_label"],
                    type_id=demo_id("type", row["type_code"]),
                    site_id=demo_id("site", row["site_code"]),
                    fleet_id=demo_id("fleet", row["fleet_code"]),
                ),
            )
        source = demo_id("source")
        ensure(
            connection,
            Source,
            dict(
                id=source,
                organization_id=org,
                code="DEMO-SYNTHETIC",
                source_kind="synthetic_engine",
                schema_version=manifest["schema_version"]
                if "schema_version" in manifest
                else "synthetic-engine-v1",
            ),
        )
        for row in tables["components"].to_dict("records"):
            component = demo_id("component", row["component_serial"])
            ensure(
                connection,
                Component,
                dict(
                    id=component,
                    organization_id=org,
                    serial=row["component_serial"],
                    part_id=demo_id("part", row["part_code"]),
                    kind="engine",
                ),
            )
            ensure(
                connection,
                Engine,
                dict(component_id=component, organization_id=org, engine_type="DEMO-SINGLE"),
            )
            for channel, unit in (
                ("temperature_c", "degC"),
                ("oil_pressure_kpa", "kPa"),
                ("vibration_mm_s", "mm/s"),
            ):
                ensure(
                    connection,
                    Sensor,
                    dict(
                        id=demo_id("sensor", row["component_serial"] + ":" + channel),
                        organization_id=org,
                        component_id=component,
                        source_id=source,
                        channel_code=channel,
                        unit=unit,
                        essential=True,
                    ),
                )
        for row in tables["installations"].to_dict("records"):
            ensure(
                connection,
                Installation,
                dict(
                    id=UUID(row["installation_id"]),
                    organization_id=org,
                    aircraft_id=UUID(row["aircraft_id"]),
                    component_id=demo_id("component", row["component_serial"]),
                    position="engine",
                    installed_at=pd.Timestamp(row["installed_at"]).to_pydatetime(),
                    removed_at=None
                    if pd.isna(row["removed_at"])
                    else pd.Timestamp(row["removed_at"]).to_pydatetime(),
                    initial_hours=row["initial_age_hours"],
                    initial_cycles=0,
                ),
            )
        templates = tables["task_templates"].to_dict("records")
        for template in templates:
            code = template["template_code"]
            ensure(
                connection,
                ProcedureRevision,
                dict(
                    id=demo_id("procedure", code),
                    organization_id=org,
                    code=code,
                    revision="demo-v1",
                    authority_label="Fictional demo template; no airworthiness authority",
                    duration_slots=sum(
                        template[k] for k in ("work_hours", "inspection_hours", "release_hours")
                    ),
                    skills=list(set([template["skill_pool"], template["inspection_pool"]])),
                    part_requirements=[{"part_code": "DEMO-ENGINE", "quantity": 1}]
                    if code == "engine_exchange"
                    else [],
                ),
            )
        for row in tables["part_compatibility"].to_dict("records"):
            for template in templates:
                key = row["part_code"] + ":" + row["type_code"] + ":" + template["template_code"]
                ensure(
                    connection,
                    PartCompatibility,
                    dict(
                        id=demo_id("compatibility", key),
                        organization_id=org,
                        part_id=demo_id("part", row["part_code"]),
                        aircraft_type_id=demo_id("type", row["type_code"]),
                        procedure_revision_id=demo_id("procedure", template["template_code"]),
                    ),
                )
        stock = tables["stock_movements"]
        recorded = pd.to_datetime(stock.recorded_at, utc=True) <= cutoff
        occurred = pd.to_datetime(stock.event_time, utc=True) <= cutoff
        visible = stock.loc[recorded & occurred].sort_values("event_time")
        for code, group in visible.groupby("part_code"):
            if (group.quantity_delta.cumsum() < 0).any():
                raise ValueError("negative source stock ledger")
            for site in sorted(set(aircraft.site_code)):
                if len(set(aircraft.site_code)) != 1:
                    raise ValueError("multi-site source stock needs explicit location mapping")
                ensure(
                    connection,
                    Inventory,
                    dict(
                        id=demo_id("inventory", code + ":" + site),
                        organization_id=org,
                        part_id=demo_id("part", code),
                        site_id=demo_id("site", site),
                        condition="serviceable",
                        on_hand=int(group.quantity_delta.sum()),
                        reserved=0,
                        quarantined=0,
                        version=0,
                    ),
                )
        pools = {
            key: group.worker_id.tolist() for key, group in tables["workers"].groupby("skill_pool")
        }
        for site in sorted(set(aircraft.site_code)):
            ensure(
                connection,
                ResourceCalendar,
                dict(
                    id=demo_id("calendar", site),
                    organization_id=org,
                    site_id=demo_id("site", site),
                    slot_start=cutoff,
                    slot_end=cutoff + timedelta(hours=1),
                    bay_capacity=len(tables["bays"]),
                    skill_pools=pools,
                    version=0,
                ),
            )
        ensure(
            connection,
            PolicyVersion,
            dict(
                id=demo_id("policy"),
                organization_id=org,
                code="reference-only",
                version="demo-v1",
                content_hash=manifest["content_sha256"],
                applicability={"no_model": True},
                effective_at=cutoff,
            ),
        )
        for fleet_code, group in aircraft.groupby("fleet_code"):
            count = len(group)
            snapshot = demo_id("snapshot", fleet_code)
            ensure(
                connection,
                FleetSnapshot,
                dict(
                    id=snapshot,
                    organization_id=org,
                    fleet_id=demo_id("fleet", fleet_code),
                    as_of=cutoff,
                    source_cutoff=cutoff,
                    scope="recorded",
                    counts={
                        "serviceable": 0,
                        "maintenance": 0,
                        "grounded_other": 0,
                        "unknown": count,
                    },
                    total=count,
                    serviceable=0,
                    maintenance=0,
                    grounded_other=0,
                    unknown=count,
                    inventory_state={
                        "source_manifest": manifest["content_sha256"],
                        "on_hand": int(visible.quantity_delta.sum()),
                        "reserved": 0,
                    },
                    configuration_hash=manifest["content_sha256"],
                    versions={"seed": "reference-seed-v1"},
                ),
            )
            for row in group.to_dict("records"):
                plane = UUID(row["aircraft_id"])
                ensure(
                    connection,
                    AircraftStatusEvent,
                    dict(
                        id=demo_id("status", str(plane)),
                        organization_id=org,
                        aircraft_id=plane,
                        occurred_at=cutoff,
                        recorded_at=cutoff,
                        status="unknown",
                        reason="Reference import; historical release evidence awaits ingestion",
                        actor_id=user,
                        version=0,
                    ),
                )
                ensure(
                    connection,
                    AircraftSnapshot,
                    dict(
                        id=demo_id("aircraft-snapshot", str(plane)),
                        organization_id=org,
                        fleet_snapshot_id=snapshot,
                        aircraft_id=plane,
                        status="unknown",
                        source_cutoff=cutoff,
                        evidence={"manifest": manifest["content_sha256"]},
                    ),
                )
        counts = {
            "aircraft": len(aircraft),
            "component": len(tables["components"]),
            "installation": len(tables["installations"]),
            "sensor": len(tables["components"]) * 3,
        }
        ensure(
            connection,
            ImportBatch,
            dict(
                id=demo_id("seed-batch"),
                organization_id=org,
                source_id=source,
                checksum=manifest["content_sha256"],
                manifest_hash=manifest["content_sha256"],
                kind="reference_seed",
                state="completed",
                total_rows=sum(counts.values()),
                accepted_rows=sum(counts.values()),
                quarantined_rows=0,
                summary=counts,
            ),
        )
    from fleetiq_data.verify_db import verify_database

    return verify_database(connection, org)


def demo_credentials(settings):
    path = settings.project_root / ".secrets/demo_login.json"
    if not path.exists():
        payload = {
            "organization_code": "FLEETIQ-DEMO",
            "subject": "demo-admin",
            "password": secrets.token_urlsafe(32),
        }
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
            json.dump(payload, f)
    if path.stat().st_mode & 0o077:
        raise PermissionError("demo credentials must be private")
    return json.loads(path.read_text())


def grant_demo_import(connection):
    """Trusted provisioning only: explicit local demo import rights, no sign-off rights."""
    org, user, role = demo_id("organization"), demo_id("user"), demo_id("import-role")
    # Correct only the previous known demo permission label, never custom assignments.
    connection.execute(
        update(Role)
        .where(
            Role.id == demo_id("role"),
            Role.organization_id == org,
            Role.permissions == ["identity:manage"],
        )
        .values(permissions=["users:manage"])
    )
    ensure(
        connection,
        Role,
        dict(
            id=role, organization_id=org, code="demo-data-importer", permissions=["dataset:import"]
        ),
    )
    ensure(
        connection,
        RoleAssignment,
        dict(
            id=demo_id("import-role-assignment"),
            organization_id=org,
            user_id=user,
            role_id=role,
            scope_kind="organization",
        ),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--demo-only", action="store_true")
    parser.add_argument("--grant-import", action="store_true")
    args = parser.parse_args()
    if not args.demo_only or not args.input.resolve().is_relative_to(ROOT / "data/synthetic"):
        parser.error("explicit demo-only mode and data/synthetic input required")
    settings = Settings()
    credentials = demo_credentials(settings)
    engine = create_engine(settings.migration_database_url.get_secret_value(), hide_parameters=True)
    try:
        with engine.begin() as c:
            result = seed_database(
                c,
                args.input,
                demo_only=True,
                password_hash=PasswordHash.recommended().hash(credentials["password"]),
            )
            if args.grant_import:
                grant_demo_import(c)
        print(json.dumps(result, indent=2))
        print("Demo login is stored privately in .secrets/demo_login.json; no credentials printed.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
