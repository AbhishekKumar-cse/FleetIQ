import numpy as np
import pytest
from fleetiq_evaluation.anomaly import persistent_alerts
from fleetiq_evaluation.splits import ROOT
from fleetiq_training.anomaly import fit_context, fixture, quality, vectors
from fleetiq_training.classification import experiment


def row(hour, *, state="valid", anomalous=True, stream="a"):
    return dict(hour=hour, stream=stream, quality=state, anomalous=anomalous)


def test_invalid_resets_persistence_and_never_means_healthy_or_diagnosis():
    evidence = [row(0), row(1, state="missing_essential"), row(2), row(3), row(4), row(9)]
    result = persistent_alerts(evidence, 2, 6)
    assert [r["hour"] for r in result if r["review_alert"]] == [3]
    assert result[1]["state"] == "abstain"
    assert all(r["diagnosis"] is None for r in result)
    with pytest.raises(ValueError, match="Duplicate"):
        persistent_alerts([row(0), row(0)], 2, 6)


def test_cooldown_and_installation_isolation():
    evidence = [row(i) for i in range(10)] + [row(0, stream="b")]
    result = persistent_alerts(evidence, 2, 6)
    assert [r["hour"] for r in result if r["review_alert"]] == [1, 7]


def test_missing_impossible_units_and_context_abstain_but_extremes_survive():
    cfg = experiment(ROOT / "config/experiments.yaml")
    frame = fixture(cfg, "fit", engines=1)
    context = fit_context(frame)
    source = frame.iloc[20].to_dict()
    assert quality(source, ("F", "kPa", "mm/s")) == "unit_mismatch"
    assert quality(source | {"workload": 5}) == "out_of_context"
    for value, expected in [
        (np.nan, "missing_essential"),
        (2000, "impossible_value"),
        (450, "valid"),
    ]:
        modified = frame.copy()
        modified.loc[modified.hour == 20, "temperature_c"] = value
        rows = vectors(modified, context)
        assert rows[20]["quality"] == expected
        if expected == "valid":
            assert rows[20]["vector"][0] > 100
        else:
            assert rows[20]["vector"] is None
            assert rows[21]["quality"] == "short_history"
