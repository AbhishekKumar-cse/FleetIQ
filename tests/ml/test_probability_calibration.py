import numpy as np
import pandas as pd
import pytest
from fleetiq_training.calibration import fit_sigmoid, probabilities, reliability


def rows(count):
    return pd.DataFrame(
        dict(
            stream=[f"cal:{i}" for i in range(count) for _ in range(2)],
            cycle=[30, 40] * count,
            failure_within_horizon=[False, True] * count,
        )
    )


def test_calibration_is_disjoint_natural_prevalence_and_range_bounded():
    frame = rows(25)
    scores = np.tile([0.3, 0.8], 25)
    fit = fit_sigmoid(
        scores,
        frame,
        fit_engines=["fit"],
        tune_engines=["tune"],
        calibration_engines=frame.stream.unique(),
        minimum_events=20,
        seed=1,
    )
    values = probabilities(scores, fit)["probability"]
    assert fit["supported"] and np.all((values >= 0) & (values <= 1))
    assert len(reliability(frame, values)["bins"]) == 10
    with pytest.raises(ValueError, match="overlaps"):
        fit_sigmoid(
            scores,
            frame,
            fit_engines=["cal:0"],
            tune_engines=[],
            calibration_engines=frame.stream.unique(),
            minimum_events=20,
            seed=1,
        )


def test_many_correlated_windows_do_not_override_independent_event_gate():
    frame = rows(18)
    scores = np.tile([0.2, 0.9], 18)
    fit = fit_sigmoid(
        scores,
        frame,
        fit_engines=["fit"],
        tune_engines=["tune"],
        calibration_engines=frame.stream.unique(),
        minimum_events=20,
        seed=1,
    )
    assert not fit["supported"] and fit["parameters"] is None
    assert probabilities(scores, fit)["probability"] is None
