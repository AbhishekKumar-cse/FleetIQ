from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fleetiq_evaluation.splits import ROOT
from fleetiq_training.classification import (
    experiment,
    fit_logistic,
    metrics,
    predict_controlled,
    select_threshold,
    train,
)


def fixture():
    x = np.array([[0.0, 1], [1, 1], [2, 1], [3, 1], [4, 1], [5, 1]])
    return x, np.array([0, 0, 0, 1, 1, 1])


def test_logistic_fit_native_cycle_scores_and_controlled_repeat():
    cfg = experiment(ROOT / "config/experiments.yaml")
    x, y = fixture()
    first = fit_logistic(x, y, cfg)
    assert first == fit_logistic(x, y, cfg)
    assert np.all(np.diff(predict_controlled(first, x)) > 0)
    with pytest.raises(ValueError, match="classes"):
        fit_logistic(x, np.zeros(6), cfg)
    with pytest.raises(ValueError, match="Synthetic"):
        train(cfg, Path("unused"), track="synthetic_engine_demo")


def test_event_metrics_and_tune_budget_not_window_accuracy():
    rows = pd.DataFrame(
        dict(
            stream=["a"] * 3 + ["b"] * 3,
            cycle=[30, 40, 50] * 2,
            event_cycle=[60] * 6,
            rul_cycles=[30, 20, 10] * 2,
            failure_within_horizon=[False, True, True] * 2,
        )
    )
    scores = np.array([0.1, 0.4, 0.9, 0.2, 0.5, 0.8])
    result = select_threshold(rows, scores, 0)
    assert result["event_recall"] == 1 and result["false_alerts"] == 0
    assert result["independent_positive_events"] == 2
    single = rows.assign(failure_within_horizon=False)
    assert metrics(single, scores, 0.5)["average_precision"] is None
