import io
import json

import pytest
from fleetiq_data.manifest import persist_dataset
from fleetiq_data.seed import seed_database
from fleetiq_data.synthetic.fleet import generate_fleet, load_demo_config
from fleetiq_domain.models.assets import Aircraft
from sqlalchemy import create_engine, func, select

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def demo_input(tmp_path_factory):
    root = tmp_path_factory.mktemp("demo_seed")
    config = load_demo_config().model_copy(update={"aircraft_count": 3, "hours": 24})
    scenario = generate_fleet(config)
    files = {}
    for name, frame in scenario.observed.items():
        buffer = io.BytesIO()
        frame.to_parquet(buffer, index=False)
        files[f"observed/{name}.parquet"] = buffer.getvalue()
    path = root / "data/synthetic/seed"
    persist_dataset(
        path,
        files,
        {
            "manifest_version": "dataset-manifest-v1",
            "track": "synthetic_engine_demo",
            "cutoff": scenario.cutoff.isoformat(),
        },
        root,
    )
    return path


def test_repeatable_seed_and_matched_relations(isolated_database, demo_input):
    engine = create_engine(isolated_database[0])
    try:
        with engine.begin() as c:
            with pytest.raises(ValueError, match="demo-only"):
                seed_database(c, demo_input)
            first = seed_database(c, demo_input, demo_only=True)
            second = seed_database(c, demo_input, demo_only=True)
            assert first == second
            assert first["counts"]["aircraft"] == 3
            assert first["counts"]["sensor"] == first["counts"]["component"] * 3
            assert first["invalid_reading_installations"] == first["unvalidated_constraints"] == 0
            assert c.scalar(select(func.count()).select_from(Aircraft)) == 3
    finally:
        engine.dispose()


def test_seed_preflight_rejects_manifest_tampering(isolated_database, demo_input, tmp_path):
    import shutil

    shutil.copytree(demo_input, tmp_path / "bad")
    manifest = tmp_path / "bad/manifest.json"
    data = json.loads(manifest.read_text())
    data["cutoff"] = "2026-01-01T00:00:00+00:00"
    manifest.write_text(json.dumps(data))
    engine = create_engine(isolated_database[0])
    try:
        with engine.begin() as c:
            with pytest.raises(ValueError, match="checksum"):
                seed_database(c, tmp_path / "bad", demo_only=True)
            assert c.scalar(select(func.count()).select_from(Aircraft)) == 0
    finally:
        engine.dispose()
