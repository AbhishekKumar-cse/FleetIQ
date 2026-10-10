import numpy as np
import pytest
from fleetiq_domain.explanations import (
    component_ensemble_explanation,
    tree_explanation,
    unusual_channels,
)
from xgboost import XGBClassifier, XGBRegressor


def test_tree_reconstruction_for_cycles_and_log_odds():
    x = np.arange(120, dtype=float).reshape(40, 3)
    for model, target, scale in (
        (XGBRegressor(n_estimators=10, n_jobs=2), x[:, 0], "cycles"),
        (XGBClassifier(n_estimators=10, n_jobs=2), x[:, 0] > 60, "raw_log_odds"),
    ):
        model.fit(x, target)
        result = tree_explanation(
            model,
            x[:3],
            ["a", "b", "c"],
            x[::4],
            scale=scale,
            model_hash="a" * 64,
            feature_version="test",
        )
        assert np.allclose(
            np.array(result["base_values"]) + np.array(result["effects"]).sum(1),
            result["raw_outputs"],
            atol=2e-5,
        )
        assert result["percentages"] is None and not result["causal"]


def test_ensemble_refuses_to_aggregate_raw_effects_as_probability_shares():
    components = [
        dict(supported=True, scale="raw_log_odds", base_values=[0], effects=[[v]], raw_outputs=[v])
        for v in (2.0, -1.0)
    ]
    score = sum(0.5 / (1 + np.exp(-v)) for v in (2.0, -1.0))
    result = component_ensemble_explanation(components, [0.5, 0.5], [score])
    assert not result["supported"] and result["aggregate_effects"] is None
    with pytest.raises(ValueError, match="weighted"):
        component_ensemble_explanation(components, [0.5, 0.5], [0.99])
    assert (
        unusual_channels([1, -4, 2, 0, 0, 0, 0, 0, 0])["channels"][0]["channel"] == "pressure_kpa"
    )
