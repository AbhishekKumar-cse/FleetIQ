from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from fleetiq_data.eda import training_group
from fleetiq_features.__main__ import benchmark_inputs, export
from fleetiq_features.context import ContextRecord
from fleetiq_features.domain import TechnicalRecord
from fleetiq_features.pipeline import (
    PipelineFit,
    SnapshotInput,
    expected_units,
    fit_pipeline,
    snapshot,
)
from fleetiq_features.schema import Sample


def fixture():
    unit = next(unit for unit in range(1, 100) if training_group(str(unit)))
    return pd.DataFrame(
        [
            dict(
                unit_id=unit,
                cycle=cycle,
                **{f"setting_{i}": 0 for i in range(1, 4)},
                **{
                    f"sensor_{i}": (None if i == 2 else float(cycle if i == 1 else 7))
                    for i in range(1, 22)
                },
            )
            for cycle in range(1, 5)
        ]
    )


def test_offline_online_parity_short_constant_missing_and_frozen_artifact(tmp_path):
    frame = fixture()
    manifest = export(frame, tmp_path)
    fit = PipelineFit.load(tmp_path / "preprocessing.json")
    saved = pd.read_parquet(tmp_path / "snapshots.parquet")
    for spec, (_, row) in zip(benchmark_inputs(frame), saved.iterrows(), strict=True):
        online = snapshot(spec, fit)
        assert tuple(row[name] for name in fit.names) == online["vector"]
        assert row.input_hash == online["input_hash"]
        assert online["short_history"]
        assert not online["supported"]
        assert any(online["imputed_features"])
    assert manifest["official_test_accessed"] is False
    assert "sensor_2.mean" in fit.all_missing


def test_future_append_ood_units_layout_and_training_boundary(tmp_path):
    specs = list(benchmark_inputs(fixture()))
    fit = fit_pipeline(specs, track="cmapss_benchmark")
    spec = specs[-1]
    before = snapshot(spec, fit)
    future = {
        name: (*rows, Sample(100, 999, 100, stream=spec.stream))
        for name, rows in spec.channels.items()
    }
    assert snapshot(replace(spec, channels=future), fit) == before
    changed = dict(spec.channels)
    changed["sensor_1"] = (Sample(4, 1e6, 4, stream=spec.stream),)
    assert snapshot(replace(spec, channels=changed), fit)["fit_hash"] == before["fit_hash"]
    with pytest.raises(ValueError, match="layout"):
        snapshot(replace(spec, units=("degC",) * 21), fit)
    with pytest.raises(ValueError, match="layout"):
        snapshot(replace(spec, channels=dict(reversed(list(spec.channels.items())))), fit)
    nontrain = next(str(i) for i in range(1, 100) if not training_group(str(i)))
    with pytest.raises(ValueError, match="training"):
        fit_pipeline([replace(spec, group=nontrain)], track="cmapss_benchmark")
    with pytest.raises(ValueError, match="Official test"):
        fit_pipeline(
            [replace(spec, stream=f"NASA:FD001:test:{spec.group}")], track="cmapss_benchmark"
        )
    with pytest.raises(ValueError):
        snapshot(spec, replace(fit, medians=tuple([0] * len(fit.names))))


def test_synthetic_context_parity_and_recording_cutoff(tmp_path):
    at = datetime(2026, 1, 1, tzinfo=UTC)
    group = next(str(i) for i in range(1, 100) if training_group(str(i)))
    spec = SnapshotInput(
        "synthetic_engine_demo",
        "install",
        group,
        at,
        {
            name: (Sample(at, value, at, stream="install"),)
            for name, value in [
                ("temperature_c", 100),
                ("oil_pressure_kpa", 300),
                ("vibration_mm_s", 2),
            ]
        },
        expected_units("synthetic_engine_demo"),
        technical=(
            TechnicalRecord("install", "installed", at, at, component_age_hours=40),
            TechnicalRecord("install", "usage", at, at, hours=2, cycles=1),
        ),
        context=(ContextRecord("install", at, at, 1, 20, "medium"),),
    )
    fit = fit_pipeline([spec], track=spec.track)
    fit.save(tmp_path / "fit.json")
    before = snapshot(spec, fit)
    assert before == snapshot(spec, PipelineFit.load(tmp_path / "fit.json"))
    assert before["features"]["domain.known_component_age_hours"] == 42
    later = at + timedelta(days=1)
    changed = replace(
        spec,
        technical=(*spec.technical, TechnicalRecord("install", "maintenance", at, later)),
        context=(*spec.context, ContextRecord("install", at, later, 999, 999, "new")),
    )
    assert snapshot(changed, fit) == before
    delayed = replace(
        spec,
        as_of=at + timedelta(seconds=5),
        channels={
            name: (replace(rows[0], recorded_at=at + timedelta(seconds=5)),)
            for name, rows in spec.channels.items()
        },
    )
    delayed_fit = fit_pipeline([delayed, delayed], track=spec.track)
    assert len(delayed_fit.baselines) == 3
    assert snapshot(delayed, delayed_fit)["features"]["temperature_c.w3600.residual"] == 0
