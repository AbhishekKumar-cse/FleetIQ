"""Real app-role imports: durable chunks, raw evidence and atomic rejection."""

import json
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest
import sqlalchemy as sa
from fleetiq_api.settings import Settings
from fleetiq_data.importer import import_file
from fleetiq_data.quality.dedup import DuplicateConflict, raw_json
from fleetiq_data.seed import demo_id, grant_demo_import, seed_database
from fleetiq_domain.authorization import Forbidden, Principal
from fleetiq_domain.models.assets import AircraftType, Site
from fleetiq_domain.models.inventory import Inventory, SparePart, StockMovement
from fleetiq_domain.models.operations import ImportBatch, Job, Quarantine, Role, RoleAssignment
from fleetiq_domain.models.telemetry import SensorReading, SourceEventReceipt
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def importing(isolated_database, tmp_path):
    migration = sa.create_engine(isolated_database[0], hide_parameters=True)
    with migration.begin() as c:
        seed_database(c, ROOT / "data/synthetic", demo_only=True, password_hash="$argon2id$fixture")
        grant_demo_import(c)
    app = sa.create_engine(
        make_url(Settings().database_url.get_secret_value()).set(
            database=make_url(isolated_database[0]).database
        ),
        hide_parameters=True,
    )
    frame = pd.read_parquet(ROOT / "data/synthetic/observed/sensor_observations.parquet").head(6)
    yield app, migration, tmp_path, frame, Principal(demo_id("organization"), demo_id("user"))
    app.dispose()
    migration.dispose()


def run(fixture, path, **kwargs):
    app, _, root, _, principal = fixture
    return import_file(
        app,
        principal,
        demo_id("source"),
        path,
        input_root=root,
        vault_root=root / "vault",
        **kwargs,
    )


@pytest.mark.parametrize("extension", ["json", "csv", "parquet"])
def test_formats_idempotence_and_partial_quality(importing, extension):
    app, _, root, frame, _ = importing
    frame = frame.iloc[:3].copy()
    frame.loc[frame.index[1], "vibration_mm_s"] = float("nan")
    path = root / ("rows." + extension)
    if extension == "json":
        path.write_text(json.dumps(raw_json(frame.to_dict("records"))))
    elif extension == "csv":
        frame.to_csv(path, index=False)
    else:
        frame.to_parquet(path, index=False)
    summary = run(importing, path, batch_size=2)
    assert summary["processed"] == summary["input_rows"] == 3
    assert sum(summary[k] for k in ["accepted", "flagged", "rejected", "duplicate"]) == 3
    assert summary["flagged"] >= 1 and summary["readings"] == 8
    assert run(importing, path, batch_size=2) == summary
    with app.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(SensorReading)) == 8
        assert c.scalar(sa.select(sa.func.count()).select_from(Job)) == 2
        assert c.scalar(sa.select(sa.func.count()).select_from(Quarantine)) >= 1
    archive = next((root / "vault").glob("*." + extension))
    assert archive.read_bytes() == path.read_bytes()


def test_restart_after_uncommitted_chunk_and_changed_id(importing):
    app, _, root, frame, _ = importing
    path = root / "rows.parquet"
    frame.to_parquet(path, index=False)

    def fail_second(c, summary):
        if summary["processed"] == 4:
            raise RuntimeError("Simulated crash before acknowledgment")

    with pytest.raises(RuntimeError):
        run(importing, path, batch_size=2, before_commit=fail_second)
    with app.connect() as c:
        assert (
            c.scalar(sa.select(ImportBatch.total_rows).where(ImportBatch.kind == "telemetry")) == 2
        )
        assert c.scalar(sa.select(sa.func.count()).select_from(SensorReading)) == 6
        assert c.scalar(sa.select(sa.func.count()).select_from(Job)) == 1
    summary = run(importing, path, batch_size=2)
    assert summary["processed"] == 6 and summary["readings"] == 18
    # Different raw-file checksum, same logical source event, changed timestamp.
    changed = frame.iloc[:1].copy()
    changed["measured_at"] = pd.to_datetime(changed["measured_at"], utc=True) + pd.Timedelta(
        seconds=1
    )
    changed["recorded_at"] = pd.to_datetime(changed["recorded_at"], utc=True) + pd.Timedelta(
        seconds=1
    )
    changed_path = root / "changed.parquet"
    changed.to_parquet(changed_path, index=False)
    with pytest.raises(DuplicateConflict):
        run(importing, changed_path)
    with app.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(SensorReading)) == 18
    # Same logical events in another file count as duplicates with no extra readings.
    repeat = root / "repeat.parquet"
    frame.iloc[:2].to_parquet(repeat, index=False)
    assert run(importing, repeat)["duplicate"] == 2


def test_atomic_configuration_and_authorization(importing):
    app, migration, root, _, p = importing
    with migration.begin() as c:
        type_id = c.scalar(sa.select(AircraftType.id).limit(1))
        role = c.scalar(
            sa.insert(Role)
            .values(
                organization_id=p.organization_id, code="config-import", permissions=["asset:write"]
            )
            .returning(Role.id)
        )
        c.execute(
            sa.insert(RoleAssignment).values(
                organization_id=p.organization_id,
                user_id=p.user_id,
                role_id=role,
                scope_kind="organization",
            )
        )
    path = root / "configuration.json"
    path.write_text(
        json.dumps(
            [
                {"id": str(type_id), "configuration": {"fixture": True}},
                {"id": str(uuid4()), "configuration": {}},
            ]
        )
    )
    with pytest.raises(ValueError):
        run(importing, path, kind="configuration")
    with app.connect() as c:
        assert c.scalar(
            sa.select(AircraftType.configuration).where(AircraftType.id == type_id)
        ) != {"fixture": True}
        assert (
            c.scalar(
                sa.select(sa.func.count())
                .select_from(ImportBatch)
                .where(ImportBatch.kind == "configuration")
            )
            == 0
        )
    path.write_text(json.dumps([{"id": str(type_id), "configuration": {"fixture": True}}]))
    assert run(importing, path, kind="configuration")["accepted"] == 1
    with pytest.raises(Forbidden):
        import_file(
            app,
            Principal(uuid4(), p.user_id),
            demo_id("source"),
            path,
            input_root=root,
            vault_root=root / "vault",
        )
    with pytest.raises(ValueError):
        run(importing, ROOT / "config/quality.yaml")


def test_no_valid_channel_quarantined(importing):
    app, _, root, frame, _ = importing
    frame = frame.iloc[:1].copy()
    for channel in ["temperature_c", "oil_pressure_kpa", "vibration_mm_s"]:
        frame[channel] = float("inf")
    path = root / "invalid.parquet"
    frame.to_parquet(path, index=False)
    result = run(importing, path)
    assert result["rejected"] == 1 and result["readings"] == 0
    with app.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(Quarantine)) == 1
        assert c.scalar(sa.select(sa.func.count()).select_from(SourceEventReceipt)) == 1


def test_inventory_prevalidation_and_ledger(importing):
    app, migration, root, _, p = importing
    with migration.begin() as c:
        part = c.scalar(
            sa.insert(SparePart)
            .values(
                organization_id=p.organization_id,
                code="BULK-FIXTURE",
                kind="consumable",
                serialized=False,
            )
            .returning(SparePart.id)
        )
        site = c.scalar(sa.select(Site.id).limit(1))
        inventory = c.scalar(
            sa.insert(Inventory)
            .values(
                organization_id=p.organization_id,
                part_id=part,
                site_id=site,
                condition="serviceable",
                on_hand=5,
                reserved=0,
                quarantined=0,
            )
            .returning(Inventory.id)
        )
        role = c.scalar(
            sa.insert(Role)
            .values(
                organization_id=p.organization_id,
                code="stock-import",
                permissions=["inventory:write"],
            )
            .returning(Role.id)
        )
        c.execute(
            sa.insert(RoleAssignment).values(
                organization_id=p.organization_id,
                user_id=p.user_id,
                role_id=role,
                scope_kind="organization",
            )
        )
    path = root / "stock.json"
    first = dict(id=str(inventory), version=0, quantity_delta=-2, reason="Fixture issue")
    path.write_text(json.dumps([first, dict(first, id=str(uuid4()))]))
    with pytest.raises(ValueError):
        run(importing, path, kind="inventory")
    with app.connect() as c:
        assert c.scalar(sa.select(Inventory.on_hand).where(Inventory.id == inventory)) == 5
        assert c.scalar(sa.select(sa.func.count()).select_from(StockMovement)) == 0
    path.write_text(json.dumps([first]))
    assert run(importing, path, kind="inventory")["accepted"] == 1
    assert run(importing, path, kind="inventory")["accepted"] == 1
    with app.connect() as c:
        assert c.scalar(sa.select(Inventory.on_hand).where(Inventory.id == inventory)) == 3
        assert c.scalar(sa.select(sa.func.count()).select_from(StockMovement)) == 1
