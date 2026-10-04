import json

import pytest
from fleetiq_data.generate import generate_dataset
from fleetiq_data.manifest import guarded_output, persist_dataset, sha256
from fleetiq_data.synthetic.fleet import load_demo_config


def test_independent_generations_match_hashes_and_access_boundaries(tmp_path):
    config = load_demo_config().model_copy(update={"aircraft_count": 2, "hours": 24})
    first = generate_dataset(config, 26249, tmp_path / "data/synthetic/first", tmp_path)
    second = generate_dataset(config, 26249, tmp_path / "data/synthetic/second", tmp_path)
    assert first == second
    for name, digest in first["files"].items():
        assert sha256((tmp_path / "data/synthetic/first" / name).read_bytes()) == digest
    assert any(name.startswith("evaluator/") for name in first["files"])
    assert any(name.startswith("observed/") for name in first["files"])
    assert first["benchmark_provenance"]["archive_sha256"]
    assert first["input_sha256"]["config/targets.json"]


def test_immutable_preflight_and_repeat(tmp_path):
    output = tmp_path / "data/synthetic/baseline"
    files = {"observed/example.parquet": b"original", "evaluator/example.parquet": b"truth"}
    first = persist_dataset(output, files, {"seed": 1}, tmp_path)
    assert persist_dataset(output, files, {"seed": 1}, tmp_path) == first
    with pytest.raises(ValueError, match="differs"):
        persist_dataset(
            output, files | {"observed/example.parquet": b"changed"}, {"seed": 1}, tmp_path
        )
    assert (output / "observed/example.parquet").read_bytes() == b"original"
    assert json.loads((output / "manifest.json").read_text()) == first


@pytest.mark.parametrize("path", ["docs/export", "data/raw/export", "data/synthetic/../../outside"])
def test_storage_root_guards(tmp_path, path):
    with pytest.raises(ValueError):
        guarded_output(tmp_path / path, tmp_path)


def test_symlink_and_internal_path_traversal_rejected(tmp_path):
    output = tmp_path / "data/synthetic"
    output.mkdir(parents=True)
    external = tmp_path / "external"
    external.mkdir()
    (output / "link").symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError):
        guarded_output(output / "link", tmp_path)
    with pytest.raises(ValueError):
        persist_dataset(output, {"observed/../../private": b"x"}, {}, tmp_path)
