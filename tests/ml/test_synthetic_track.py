import json

import numpy as np
import pandas as pd
import pytest
from fleetiq_data.synthetic.degradation import generate_trajectory
from fleetiq_evaluation.splits import ROOT
from fleetiq_registry.synthetic import SyntheticHourBundle
from fleetiq_training.anomaly import fit_context, fixture
from fleetiq_training.classification import experiment
from fleetiq_training.rul_intervals import calibrate_engine_intervals
from fleetiq_training.synthetic_track import NAMES, features


def test_causal_observed_only_features_and_latent_truth_rejected():
    cfg = experiment(ROOT / "config/experiments.yaml")
    context = fit_context(fixture(cfg, "fit", engines=4))
    trajectory = generate_trajectory(seed=803002, hours=20)
    prefix = features(trajectory.observed.iloc[:12], context)[-1]
    full = features(trajectory.observed, context)[11]
    assert prefix == full
    assert len(prefix["vector"]) == len(NAMES)
    assert not any("damage" in n or "failure" in n or "serial" in n or "rul" in n for n in NAMES)
    with pytest.raises(ValueError, match="truth forbidden"):
        features(
            trajectory.observed.assign(latent_damage=trajectory.evaluator.latent_damage), context
        )


def test_native_hour_interval_has_no_cycle_fields_and_no_mixed_units():
    frame = pd.DataFrame(
        [
            dict(stream=f"cal:{i}", hour=12, rul_hours=20.0, life_unit="operating_hours")
            for i in range(20)
        ]
    )
    kwargs = dict(
        fit_engines=["fit:1"],
        tune_engines=["tune:1"],
        calibration_engines=list(frame.stream),
        life_unit="operating_hours",
        scope="synthetic_sensor_model:hourly_stochastic_failure:uncapped",
    )
    report = calibrate_engine_intervals(frame, np.zeros(len(frame)), **kwargs)
    assert report["candidate_radius_hours"] == 20
    assert "candidate_radius_cycles" not in report and report["life_unit"] == "operating_hours"
    with pytest.raises(ValueError, match="native-cycle"):
        calibrate_engine_intervals(frame.assign(life_unit="cycles"), np.zeros(len(frame)), **kwargs)


def test_frozen_separate_bundle_independent_seed_scopes_and_native_parity():
    path = ROOT / "docs/exports/synthetic_evaluation.json"
    if not path.exists():
        pytest.skip("Run the synthetic-track orchestration before bundle acceptance")
    report = json.loads(path.read_text())
    roles = report["roles"]
    for role, scope in roles.items():
        assert not any(
            set(scope["streams"]) & set(other["streams"])
            for name, other in roles.items()
            if name != role
        )
    assert report["selection_hash"] == report["final_selection_hash"]
    assert report["rul"]["life_unit"] == "operating_hours"
    bundle = SyntheticHourBundle(ROOT / report["bundle_path"], report["approved_digest"])
    observed = generate_trajectory(seed=600123, hours=20, initial_damage=0.5).observed
    result = bundle.predict(observed)
    assert np.isfinite(result["output"]["remaining_operating_hours"])
    assert result["output"]["calibrated_probability"] is None
    assert result["output"]["rul_interval"] is None
    with pytest.raises(ValueError, match="NASA"):
        bundle.predict(observed, schema_version="cmapss-v1", unit="cycles")
    loaded = SyntheticHourBundle(ROOT / report["bundle_path"], report["approved_digest"])
    assert result == loaded.predict(observed)
