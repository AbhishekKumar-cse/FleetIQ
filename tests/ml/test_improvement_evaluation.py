import numpy as np
import pandas as pd
import pytest
from fleetiq_evaluation.improvement import (
    endpoint_rows,
    evaluate,
    independent_intervals,
    validate_selection,
)
from fleetiq_evaluation.splits import content_hash
from fleetiq_training.classification import write_json
from fleetiq_training.improvement import fit_pipeline, save_pipeline


def test_fresh_endpoint_labels_and_engine_intervals(tmp_path):
    frame = pd.DataFrame({"unit_id": [1, 1, 2, 2, 3, 3], "cycle": [1, 45, 1, 90, 1, 100]})
    path = tmp_path / "rul.txt"
    path.write_text("15\n31\n0\n")
    rows = endpoint_rows(frame, path, {"horizon_cycles": 30})
    assert rows.failure_within_horizon.tolist() == [1, 0, 0]
    assert rows.stream.tolist() == [f"NASA:FD003:test:{i}" for i in (1, 2, 3)]
    scores = np.array([0.8, 0.2, 0.1, 0.9])
    ci = independent_intervals([1, 0, 0, 1], scores, 0.5, seed=26249, repetitions=20)
    assert ci["unit"] == "whole_engine_endpoint"
    assert ci["intervals"]["accuracy"] == [1.0, 1.0]
    with pytest.raises(ValueError, match="predeclared untouched"):
        evaluate(tmp_path, tmp_path, {"final_subset": "FD001", "horizon_cycles": 30})


def test_selected_artifacts_and_configuration_are_frozen(tmp_path):
    cfg = {"threads": 1, "seed": 26249}
    x = np.arange(80, dtype=float).reshape(40, 2)
    y = np.tile([0, 1], 20)
    model, _ = fit_pipeline("baseline_xgboost", {"iterations": 5, "depth": 2}, cfg, x, y)
    artifact = save_pipeline(tmp_path / "baseline_xgboost", "baseline_xgboost", model, ["a", "b"])
    body = dict(
        experiment_hash=content_hash(cfg),
        final_test_accessed=False,
        groups={"fit": ["fit-engine"], "tune": ["tune-engine"], "calibration": ["cal-engine"]},
        candidate={"components": ["baseline_xgboost"]},
        candidates={"baseline_xgboost": {"artifact": artifact}},
    )
    body["selection_hash"] = content_hash(body)
    write_json(tmp_path / "selection.json", body)
    assert validate_selection(tmp_path, cfg) == body
    with pytest.raises(ValueError, match="integrity"):
        validate_selection(tmp_path, cfg | {"seed": 1})
    write_json(tmp_path / "baseline_xgboost/component.json", artifact | {"model_hash": "changed"})
    with pytest.raises(ValueError, match="changed after selection"):
        validate_selection(tmp_path, cfg)
