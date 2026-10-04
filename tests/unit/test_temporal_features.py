from datetime import UTC, datetime, timedelta

import pytest
from fleetiq_features import Sample
from fleetiq_features.schema import select_window
from fleetiq_features.temporal import (
    fit_baseline,
    temporal_feature_names,
    temporal_features,
    temporal_statistics,
)


def test_hand_checked_irregular_time_slope_derivative_and_ewma():
    at = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        Sample(at + timedelta(seconds=t), value, at + timedelta(seconds=t), stream="same")
        for t, value in [(0, 1), (2, 5), (5, 11)]
    ]
    baseline = fit_baseline(
        [2, 4],
        channel="temperature_c",
        track="synthetic_engine_demo",
        partition="train",
        training_groups=["aircraft-A"],
        version="baseline-1",
    )
    result = temporal_statistics(rows, track="synthetic_engine_demo", alpha=0.5, baseline=baseline)
    assert result["rolling_mean"] == pytest.approx(17 / 3)
    assert result["slope"] == pytest.approx(2)
    assert result["lag"] == 5 and result["delta"] == 6 and result["derivative"] == 2
    assert result["ewma"] == 7 and result["residual"] == 8
    assert result["kurtosis_excess"] is None


def test_constants_short_windows_and_standardized_moments():
    def result(values):
        return temporal_statistics(
            [Sample(i, value, i) for i, value in enumerate(values, 1)],
            track="cmapss_benchmark",
            alpha=0.5,
        )

    constant = result([4, 4, 4, 4])
    assert constant["slope"] == constant["derivative"] == constant["rolling_std"] == 0
    assert constant["skew"] is constant["kurtosis_excess"] is None
    single = result([4])
    assert single["ewma"] == 4 and single["lag"] is single["slope"] is None
    assert all(value is None for value in result([]).values())
    assert result([-1, 0, 1])["skew"] == pytest.approx(0)
    assert result([-1, -1, 1, 1])["kurtosis_excess"] == pytest.approx(-2)
    with pytest.raises(ValueError):
        temporal_statistics([], track="cmapss_benchmark", alpha=0)


def test_future_append_native_windows_and_stable_order():
    rows = [Sample(i, i, i, stream="engine") for i in range(1, 101)]
    vector = temporal_features({"sensor_1": rows}, track="cmapss_benchmark", as_of=100)
    assert tuple(vector["features"]) == temporal_feature_names("cmapss_benchmark")
    assert len(vector["masks"]["sensor_1.w30"]["observed"]) == 30
    assert vector["features"]["sensor_1.w30.derivative"] == pytest.approx(1)
    assert (
        temporal_features(
            {
                "sensor_1": rows
                + [Sample(101, 9999, 101, stream="engine"), Sample(90, 9999, 110, stream="engine")]
            },
            track="cmapss_benchmark",
            as_of=100,
        )
        == vector
    )
    at = datetime(2026, 1, 1, tzinfo=UTC)
    samples = [
        Sample(at - timedelta(seconds=601), 999, at - timedelta(seconds=601)),
        Sample(at, 5, at),
    ]
    synthetic = temporal_features(
        {"temperature_c": samples}, track="synthetic_engine_demo", as_of=at
    )
    assert synthetic["features"]["temperature_c.w600.rolling_mean"] == 5
    assert synthetic["features"]["temperature_c.w600.slope"] is None
    assert synthetic["features"]["temperature_c.w3600.slope"] is not None
    assert synthetic["time_unit"] == "seconds"


def test_frozen_baseline_no_refit_or_validation_test_access():
    args = dict(
        channel="sensor_1", track="cmapss_benchmark", training_groups=["train-engine"], version="b1"
    )
    for partition in ["validation", "test", "calibration", "tune"]:
        with pytest.raises(ValueError):
            fit_baseline([1, 2], partition=partition, **args)
    baseline = fit_baseline([1, 3], partition="train", **args)
    assert baseline == fit_baseline([1, 3], partition="train", **args)
    vector = temporal_features(
        {"sensor_1": [Sample(1, 3, 1), Sample(2, 5, 2)]},
        track="cmapss_benchmark",
        as_of=2,
        baselines={"sensor_1": baseline},
    )
    assert vector["features"]["sensor_1.w30.residual"] == 3
    assert baseline.mean == 2
    with pytest.raises(ValueError):
        temporal_features({}, track="cmapss_benchmark", as_of=2, baselines={"sensor_2": baseline})


def test_missing_points_break_lags_and_installation_boundaries():
    rows = [Sample(1, 1, 1), Sample(2, None, 2, quality="missing"), Sample(3, 5, 3)]
    result = temporal_statistics(rows, track="cmapss_benchmark")
    assert result["lag"] is result["delta"] is result["derivative"] is None
    assert result["slope"] == 2
    with pytest.raises(ValueError):
        select_window(
            [Sample(1, 1, 1, stream="old"), Sample(2, 1, 2, stream="replacement")],
            track="cmapss_benchmark",
            as_of=2,
            window_start=0,
        )
    result = temporal_features(
        {"sensor_1": [Sample(2, 1, 2, stream="replacement")]}, track="cmapss_benchmark", as_of=2
    )
    assert result["features"]["sensor_1.w30.derivative"] is None
