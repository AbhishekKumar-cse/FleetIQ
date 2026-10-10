import numpy as np
import pandas as pd
import pytest
from fleetiq_data.targets import training_cycle_targets
from fleetiq_evaluation.splits import ROOT
from fleetiq_training.classification import experiment
from fleetiq_training.rul_baseline import (
    engine_weights,
    fit_baselines,
    predict_baseline,
    regression_metrics,
    validate_contract,
)


def toy():
    frame = training_cycle_targets(
        pd.DataFrame(dict(unit_id=[1, 1, 1, 2, 2, 2], cycle=[30, 40, 50, 30, 40, 60]))
    )
    frame["stream"] = frame.unit_id.astype(str)
    frame["observed_age"] = frame.cycle
    return frame.loc[frame.eligible].copy()


def test_native_target_terminal_exclusion_and_nonnegative_display():
    frame = toy()
    assert frame.rul_cycles.tolist() == [20, 10, 30, 20]
    assert set(frame.life_unit) == {"cycles"}
    metrics = regression_metrics(frame, np.full(4, -2.0))
    assert metrics["negative_raw_predictions"] == 4
    assert metrics["mae_cycles"] > metrics["nonnegative_display_mae_cycles"]
    cfg = experiment(ROOT / "config/experiments.yaml")
    cfg["rul_training"]["early_life_cap"] = 125
    with pytest.raises(ValueError, match="contract"):
        validate_contract(cfg)


def test_fit_only_references_and_engine_weighting():
    cfg = experiment(ROOT / "config/experiments.yaml")
    frame = toy()
    models = fit_baselines(frame, ["observed_age"], cfg)
    assert models["usage"]["expected_terminal_cycle"] == 55
    for model in models.values():
        assert np.isfinite(predict_baseline(model, frame[["observed_age"]], frame.cycle)).all()
    repeated = pd.concat([frame.loc[frame.stream == "1"]] * 10 + [frame.loc[frame.stream == "2"]])
    assert np.isclose(
        engine_weights(repeated)[repeated.stream == "1"].sum(),
        engine_weights(repeated)[repeated.stream == "2"].sum(),
    )
