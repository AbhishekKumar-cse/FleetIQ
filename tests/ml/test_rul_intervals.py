import numpy as np
import pandas as pd
import pytest
from fleetiq_training.rul_intervals import calibrate_engine_intervals, interval_prediction


def samples(engines=20, windows=3):
    return pd.DataFrame(
        [
            dict(stream=f"cal:{e}", cycle=30 + w * 10, rul_cycles=20.0, life_unit="cycles")
            for e in range(engines)
            for w in range(windows)
        ]
    )


def calibrate(frame, predictions, **kwargs):
    return calibrate_engine_intervals(
        frame,
        predictions,
        fit_engines=["fit:0"],
        tune_engines=["tune:0"],
        calibration_engines=sorted(frame.stream.unique()),
        **kwargs,
    )


def test_small_n_rank_and_declared_floor_are_unsupported():
    frame = samples(engines=3)
    report = calibrate(frame, np.zeros(len(frame)))
    assert report["rank"] == 4 and report["independent_engines"] == 3
    assert report["displayed_radius_cycles"] is None
    assert interval_prediction(report, 10)["interval"] is None
    frame = samples(engines=18)
    report = calibrate(frame, np.zeros(len(frame)))
    assert report["rank"] == 18 and not report["supported"]
    assert report["reasons"] == ["below_declared_minimum_independent_engines"]


def test_engine_max_prevents_window_inflation_and_matches_nonnegative_point():
    first = samples(engines=20, windows=1)
    expanded = samples(engines=20, windows=10)
    one = calibrate(first, np.full(len(first), -5))
    many = calibrate(expanded, np.full(len(expanded), -5))
    assert one["independent_engines"] == many["independent_engines"] == 20
    assert one["rank"] == many["rank"] == 19
    assert one["candidate_radius_cycles"] == many["candidate_radius_cycles"] == 20
    assert one["supported"] and not one["display_enabled"]
    display = one | {"display_enabled": True}
    assert interval_prediction(display, -5)["interval"] == [0, 20]
    assert interval_prediction(display, 5, shifted=True)["interval"] is None
    assert interval_prediction(display, 5, scope="synthetic_hours")["interval"] is None
    assert not interval_prediction(display, 5)["coverage_guaranteed"]


def test_overlap_duplicate_and_nonfinite_fail_closed():
    frame = samples()
    with pytest.raises(ValueError, match="disjoint"):
        calibrate_engine_intervals(
            frame,
            np.zeros(len(frame)),
            fit_engines=["cal:0"],
            tune_engines=[],
            calibration_engines=sorted(frame.stream.unique()),
        )
    with pytest.raises(ValueError, match="Duplicate"):
        calibrate(pd.concat([frame, frame.iloc[:1]]), np.zeros(len(frame) + 1))
    with pytest.raises(ValueError, match="Finite"):
        calibrate(frame, np.full(len(frame), np.nan))
