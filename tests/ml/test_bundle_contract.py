import json

import pytest
from fleetiq_registry.bundle import schema_hash, verify_files
from fleetiq_training.classification import file_hash


def test_approved_digest_native_checksums_and_path_boundaries(tmp_path):
    model = tmp_path / "model.json"
    model.write_text('{"native": true}')
    manifest = dict(files={"model.json": file_hash(model)})
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    digest = file_hash(tmp_path / "manifest.json")
    verify_files(tmp_path, manifest, digest)
    with pytest.raises(ValueError, match="approved"):
        verify_files(tmp_path, manifest, "0" * 64)
    model.write_text("tampered")
    with pytest.raises(ValueError, match="checksum"):
        verify_files(tmp_path, manifest, digest)
    with pytest.raises(ValueError, match="path"):
        verify_files(tmp_path, dict(files={"../evil.pkl": "0" * 64}), digest)


def test_schema_binds_order_track_units_and_version():
    base = schema_hash(["a", "b"], "cmapss_benchmark", "cycles", "v1")
    assert base != schema_hash(["b", "a"], "cmapss_benchmark", "cycles", "v1")
    assert base != schema_hash(["a", "b"], "cmapss_benchmark", "hours", "v1")
