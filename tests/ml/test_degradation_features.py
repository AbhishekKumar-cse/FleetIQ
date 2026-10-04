import numpy as np
import pandas as pd
import pytest
from fleetiq_training.degradation_features import endpoint_indices, engine_features


def trajectory(n=80):
    frame = pd.DataFrame({"cycle": np.arange(1, n + 1)})
    for i in range(1, 22):
        frame[f"sensor_{i}"] = np.arange(n, dtype=float) * i + 100
    for i in range(1, 4):
        frame[f"setting_{i}"] = i
    return frame


def test_degradation_features_never_use_future_and_preserve_native_slopes():
    frame = trajectory()
    prefix = engine_features(frame.iloc[:45])
    full = engine_features(frame)
    np.testing.assert_allclose(prefix, full.loc[prefix.index], atol=1e-9)
    modified = frame.copy()
    modified.loc[45:, "sensor_2"] = 1e8
    np.testing.assert_allclose(prefix, engine_features(modified).loc[prefix.index], atol=1e-9)
    assert full.loc[44, "sensor_2.w30.slope"] == pytest.approx(2)
    assert full.loc[44, "history.w60.count"] == 45
    assert np.isfinite(full.to_numpy()).all()
    with pytest.raises(ValueError, match="30 contiguous"):
        engine_features(frame.iloc[:20])


def test_endpoint_draws_deterministic_and_outcome_blind():
    rows = pd.DataFrame({"stream": ["a"] * 50 + ["b"] * 50, "rul_cycles": range(100)})
    cfg = {"seed": 26249, "endpoint_draws_per_engine": 5}
    indices = endpoint_indices(rows, cfg)
    rows["rul_cycles"] = 9999
    assert endpoint_indices(rows, cfg) == indices
    assert len(set(indices)) == 10
