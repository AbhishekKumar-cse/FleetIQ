import json

import pytest
from fleetiq_registry.bundle import Bundle, schema_hash, verify_files
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


def test_native_short_history_and_shift_abstain_before_prediction():
    bundle = Bundle.__new__(Bundle)
    bundle.hash = "a" * 64
    bundle.manifest = dict(
        tasks=dict(
            rul=dict(
                track="cmapss_benchmark",
                unit="cycles",
                horizon=0,
                names=["sensor"],
                schema_hash="b" * 64,
                version="v1",
                feature_version="v1",
            )
        )
    )
    request = dict(
        task="rul",
        track="cmapss_benchmark",
        unit="cycles",
        horizon=0,
        request_id="fixture",
        names=["sensor"],
        schema_hash="b" * 64,
        supported=True,
        native_cycle=29,
        values=[1.0],
    )
    for changes in ({}, {"native_cycle": 30, "ood": True}):
        result = bundle.predict(request | changes)
        assert result["coverage"] == "unsupported"
        assert result["output"] is None
