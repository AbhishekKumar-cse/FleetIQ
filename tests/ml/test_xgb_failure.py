from copy import deepcopy

import numpy as np
import pytest
from fleetiq_evaluation.splits import ROOT
from fleetiq_training.classification import experiment
from fleetiq_training.xgb_failure import fit_xgboost, select_candidate


def test_cpu_hist_repeat_seed_native_save_and_search_bounds(tmp_path):
    cfg = experiment(ROOT / "config/experiments.yaml")
    cfg["training"]["xgboost"] = dict(
        n_estimators=20, early_stopping_rounds=3, max_depth=[2], learning_rate=[0.1]
    )
    x = np.arange(100, dtype=float).reshape(50, 2)
    y = np.array([0] * 25 + [1] * 25)
    first, settings = fit_xgboost(x, y, x, y, cfg)
    second, again = fit_xgboost(x, y, x, y, cfg)
    assert settings == again and np.array_equal(first.predict_proba(x), second.predict_proba(x))
    first.save_model(tmp_path / "one.json")
    second.save_model(tmp_path / "two.json")
    assert (tmp_path / "one.json").read_bytes() == (tmp_path / "two.json").read_bytes()
    assert settings["device"] == "cpu" and settings["early_stopping_role"] == "tune_only"
    cfg["training"]["xgboost"]["max_depth"] = [99]
    with pytest.raises(ValueError, match="Bounded"):
        fit_xgboost(x, y, x, y, cfg)


def test_selection_keeps_simpler_model_without_predeclared_gain():
    reports = []
    for name, ap in [("logistic", 0.8), ("random_forest", 0.802), ("xgboost", 0.803)]:
        reports.append(
            dict(
                model=name,
                provenance={"anchors": "same"},
                tune=dict(average_precision=ap, event_recall=0.9, threshold=0.5),
                bootstrap={},
                measured_inference_batch_seconds=0.1,
                manifest_hash=name,
            )
        )
    chosen, _ = select_candidate(reports, 0.005)
    assert chosen["chosen"] == "logistic" and not chosen["final_test_accessed"]
    changed = deepcopy(reports)
    changed[-1]["tune"]["average_precision"] = 0.9
    assert select_candidate(changed, 0.005)[0]["chosen"] == "xgboost"
