import numpy as np
import pytest
from fleetiq_evaluation.splits import ROOT
from fleetiq_training.anomaly import fit_context, fit_forest, fixture, forest_score, vectors
from fleetiq_training.classification import experiment


def test_normal_eligibility_reproducible_forest_and_independent_degradation():
    cfg = experiment(ROOT / "config/experiments.yaml")
    cfg["anomaly_training"]["n_estimators"] = 16
    normal = fixture(cfg, "fit", engines=3)
    context = fit_context(normal)
    x = np.array([r["vector"] for r in vectors(normal, context) if r["vector"] is not None])
    first, second = fit_forest(x, cfg), fit_forest(x, cfg)
    assert first == second
    heldout = fixture(cfg, "evaluation", degrading=True, engines=2)
    bad = np.array([r["vector"] for r in vectors(heldout, context) if r["vector"] is not None])
    assert np.median(forest_score(first, bad)) > np.median(forest_score(first, x))
    assert set(normal.stream).isdisjoint(heldout.stream)
    with pytest.raises(ValueError, match="healthy"):
        fit_context(heldout)
    with pytest.raises(ValueError, match="zero-damage"):
        fixture(cfg, "fit", degrading=True)


def test_future_mutation_cannot_change_causal_vectors():
    cfg = experiment(ROOT / "config/experiments.yaml")
    frame = fixture(cfg, "fit", engines=1)
    context = fit_context(frame)
    before = vectors(frame, context)
    frame.loc[frame.hour > 20, "temperature_c"] = 999
    after = vectors(frame, context)
    assert before[:21] == after[:21]
    assert len(before[4]["vector"]) == 9
    assert before[3]["quality"] == "short_history"
