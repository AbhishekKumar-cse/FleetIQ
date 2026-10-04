import numpy as np
import pytest
from fleetiq_training.improvement import (
    blend,
    choose_threshold,
    fit_pipeline,
    load_component,
    save_pipeline,
    scores_metrics,
)


def test_threshold_rewards_each_positive_endpoint_not_single_engine_hit():
    result = choose_threshold([1, 1, 1, 0, 0, 0], [0.95, 0.7, 0.6, 0.4, 0.3, 0.2], 0.85)
    assert result["threshold"] == 0.6
    assert result["recall"] == result["precision"] == result["f1"] == 1
    with pytest.raises(ValueError, match="Two-class"):
        choose_threshold([1, 1], [0.4, 0.5], 0.85)
    with pytest.raises(ValueError, match="Finite bounded"):
        scores_metrics([1, 0], [np.nan, 0.2], 0.5)


@pytest.mark.parametrize("family", ["baseline_xgboost", "xgboost", "catboost", "lightgbm", "mlp"])
def test_native_artifact_prediction_parity_and_integrity(tmp_path, family):
    rng = np.random.default_rng(42)
    x = rng.normal(size=(160, 5))
    x[:, -1] = 1
    y = (x[:, 0] + x[:, 1] > 0).astype(int)
    cfg = {"seed": 26249, "threads": 1}
    settings = {"depth": 3, "iterations": 10, "leaves": 7, "hidden": [8], "epochs": 5}
    model, _ = fit_pipeline(family, settings, cfg, x, y)
    record = save_pipeline(tmp_path, family, model, [f"f{i}" for i in range(5)])
    names, predict = load_component(tmp_path, threads=1)
    assert len(names) == 5
    np.testing.assert_allclose(predict(x), model.predict_proba(x)[:, 1], atol=1e-7)
    with pytest.raises(ValueError, match="ordered feature"):
        predict(x[:, :4])
    with (tmp_path / record["model_file"]).open("a") as file:
        file.write("tamper")
    with pytest.raises(ValueError, match="integrity"):
        load_component(tmp_path)


def test_ensemble_controlled_predictions():
    scores = {"a": np.array([0.1, 0.9]), "b": np.array([0.3, 0.7])}
    np.testing.assert_allclose(
        blend(scores, {"kind": "blend", "components": ["a", "b"], "weights": [0.5, 0.5]}),
        [0.2, 0.8],
    )
    values = blend(
        scores,
        {"kind": "stack", "components": ["a", "b"], "coefficients": [0.5, 0.5], "intercept": 0},
    )
    assert 0 < values[0] < values[1] < 1
