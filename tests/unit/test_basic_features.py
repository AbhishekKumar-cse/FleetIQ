import math
from datetime import UTC, datetime, timedelta

import pytest
from fleetiq_features import Sample, basic_features, feature_names
from fleetiq_features.basic import statistics
from fleetiq_features.schema import select_window

TRACK = "cmapss_benchmark"


def test_hand_computed_population_statistics():
    values = statistics([1, 2, 3, 4])
    assert values == pytest.approx(
        dict(
            count=4,
            mean=2.5,
            variance=1.25,
            std=math.sqrt(1.25),
            median=2.5,
            min=1,
            max=4,
            rms=math.sqrt(7.5),
        )
    )
    assert statistics([-2, 2])["rms"] == 2
    assert statistics([5])["variance"] == statistics([5, 5])["std"] == 0
    assert statistics([])["count"] == 0
    assert all(value is None for key, value in statistics([]).items() if key != "count")


def test_masks_order_missingness_and_future_invariance():
    samples = [
        Sample(1, 1, 1),
        Sample(2, None, 2, quality="missing"),
        Sample(3, 100, 3, quality="invalid"),
        Sample(4, float("nan"), 4),
        Sample(5, 9, 5, imputed=True),
        Sample(6, 3, 6, quality="flagged"),
    ]
    vector = basic_features({"sensor_1": samples}, track=TRACK, as_of=6, window_start=0)
    assert tuple(vector["features"]) == feature_names(TRACK)
    assert vector["features"]["sensor_1.mean"] == 2
    assert vector["features"]["sensor_1.count"] == 2
    assert vector["masks"]["sensor_1"]["observed"] == [True, False, False, False, False, True]
    assert vector["masks"]["sensor_1"]["imputed"] == [False] * 4 + [True, False]
    assert vector["features"]["sensor_2.mean"] is None and not vector["supported"]
    assert (
        basic_features(
            {"sensor_1": samples + [Sample(7, 999999, 7), Sample(3, 42, 8)]},
            track=TRACK,
            as_of=6,
            window_start=0,
        )
        == vector
    )


def test_native_window_boundaries_and_recorded_cutoff():
    rows = [Sample(cycle, cycle, cycle) for cycle in range(1, 101)]
    selected = select_window(rows, track=TRACK, as_of=100, window_start=70)
    assert len(selected) == 30 and selected[0].at == 71
    at = datetime(2026, 1, 2, tzinfo=UTC)
    old = Sample(at - timedelta(seconds=30), 7, at + timedelta(seconds=10), stream="installed-A")
    args = dict(track="synthetic_engine_demo", as_of=at, window_start=at - timedelta(minutes=1))
    assert not select_window([old], **args)
    assert select_window([old], **args, source_cutoff=at + timedelta(seconds=10)) == [old]
    with pytest.raises(ValueError):
        select_window([old], track=TRACK, as_of=100, window_start=70)
    with pytest.raises(ValueError):
        select_window(
            [], track="synthetic_engine_demo", as_of=at.replace(tzinfo=None), window_start=at
        )


def test_stream_and_schema_conflicts_are_rejected():
    args = dict(track=TRACK, as_of=3, window_start=0)
    with pytest.raises(ValueError):
        basic_features({"future_failure": [Sample(1, 1, 1)]}, **args)
    with pytest.raises(ValueError):
        select_window([Sample(1, 1, 1, stream="old"), Sample(2, 2, 2, stream="new")], **args)
    with pytest.raises(ValueError):
        select_window([Sample(1, 1, 1), Sample(1, 2, 1)], **args)
    with pytest.raises(ValueError):
        select_window([Sample(2, 1, 1)], **args)
    assert select_window([Sample(1, 1, 1)] * 2, **args) == [Sample(1, 1, 1)]


def test_large_numeric_range_has_no_nan_or_infinity():
    result = statistics([1e308, 1e308])
    assert result["mean"] == result["rms"] == 1e308
    assert result["variance"] == 0
    result = statistics([-1e308, 1e308])
    assert result["variance"] is None
    assert all(value is None or math.isfinite(value) for value in result.values())
