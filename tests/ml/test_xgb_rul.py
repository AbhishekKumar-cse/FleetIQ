from copy import deepcopy

import numpy as np
import pytest
from fleetiq_evaluation.splits import ROOT
from fleetiq_training.classification import experiment
from fleetiq_training.xgb_rul import fit_candidate, select_reference, settings_grid
from test_rul_baseline import toy
from xgboost import XGBRegressor


def test_cpu_seed_native_parity_and_bounded_search(tmp_path):
    cfg = experiment(ROOT / "config/experiments.yaml")
    settings = settings_grid(cfg)[0] | {"n_estimators": 10}
    frame = toy()
    first = fit_candidate(frame, ["observed_age"], settings)
    second = fit_candidate(frame, ["observed_age"], settings)
    x = frame[["observed_age"]].to_numpy()
    assert np.array_equal(first.predict(x), second.predict(x))
    first.save_model(tmp_path / "model.json")
    loaded = XGBRegressor()
    loaded.load_model(tmp_path / "model.json")
    assert np.array_equal(first.predict(x), loaded.predict(x))
    assert settings["device"] == "cpu" and settings["tree_method"] == "hist"
    cfg["rul_training"]["xgboost"]["depths"] = [99]
    with pytest.raises(ValueError, match="Bounded"):
        settings_grid(cfg)


def test_fair_comparison_rejects_changed_scope_and_retains_simpler_model():
    provenance = dict(anchors="fixed", targets="uncapped_cycles")
    baseline = dict(
        provenance=provenance, chosen="ridge_10", tune={"ridge_10": dict(mae_cycles=20.0)}
    )
    assert select_reference(baseline, dict(mae_cycles=19.5), provenance, 1)["chosen"] == "ridge_10"
    assert select_reference(baseline, dict(mae_cycles=18), provenance, 1)["chosen"] == "xgboost"
    altered = deepcopy(provenance) | {"targets": "capped"}
    with pytest.raises(ValueError, match="share"):
        select_reference(baseline, dict(mae_cycles=1), altered, 1)
