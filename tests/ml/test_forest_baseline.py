from copy import deepcopy

import numpy as np
import pandas as pd
import pytest
from fleetiq_evaluation.splits import ROOT
from fleetiq_training.classification import engine_bootstrap, experiment, predict_controlled
from fleetiq_training.random_forest import compare_reports, fit_forest


def test_bounded_forest_reproduces_controlled_predictions():
    cfg = experiment(ROOT / "config/experiments.yaml")
    cfg["training"]["random_forest"] = dict(
        n_estimators=8, max_depth=3, min_samples_leaf=1, max_features="sqrt"
    )
    x = np.arange(80, dtype=float).reshape(40, 2)
    y = np.array([0] * 20 + [1] * 20)
    first = fit_forest(x, y, cfg)
    assert first == fit_forest(x, y, cfg)
    assert predict_controlled(first, x).shape == (40,)
    cfg["training"]["random_forest"]["max_depth"] = 99
    with pytest.raises(ValueError, match="Bounded"):
        fit_forest(x, y, cfg)


def test_comparison_rejects_other_samples_and_bootstraps_whole_engines():
    report = dict(
        model="logistic",
        provenance=dict(split_hash="same", anchors="same"),
        tune={},
        bootstrap={},
        measured_inference_batch_seconds=0.1,
        manifest_hash="model",
    )
    other = deepcopy(report)
    other["model"] = "random_forest"
    assert compare_reports([report, other])["role"] == "tune_only"
    other["provenance"]["anchors"] = "different"
    with pytest.raises(ValueError, match="samples"):
        compare_reports([report, other])
    rows = pd.DataFrame(
        dict(
            stream=["a", "a", "b", "b"],
            cycle=[30, 40] * 2,
            event_cycle=[50] * 4,
            rul_cycles=[20, 10] * 2,
            failure_within_horizon=[False, True] * 2,
        )
    )
    result = engine_bootstrap(rows, [0.1, 0.9, 0.2, 0.8], 0.5, seed=1, repetitions=10)
    assert result["independent_engines"] == 2 and result["resampling_unit"] == "whole_engine_event"
