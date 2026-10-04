import numpy as np
import pandas as pd
import pytest
from fleetiq_evaluation.classification import final_labels, once
from fleetiq_training.classification import engine_bootstrap, metrics


def test_official_native_endpoint_mapping_qualifies_overlapping_ids():
    observed = pd.DataFrame(
        [
            dict(
                unit_id=unit,
                cycle=cycle,
                **{f"setting_{i}": 0 for i in range(1, 4)},
                **{f"sensor_{i}": 1 for i in range(1, 22)},
            )
            for unit in (1, 2, 3)
            for cycle in (30, 40)
        ]
    )
    rows = final_labels(observed, [10, 40, 0])
    assert rows.stream.iloc[0] == "NASA:FD001:test:1"
    assert rows.event_cycle.tolist() == [50, 80, 40]
    assert rows.failure_within_horizon.tolist() == [True, False, False]
    assert rows.eligible.tolist() == [True, True, False]
    with pytest.raises(ValueError, match="mapping"):
        final_labels(observed, [10, 40])
    eligible = rows[rows.eligible]
    result = metrics(eligible, np.array([0.9, 0.8]), 0.85, exposure=0)
    assert result["event_recall"] == 1 and result["false_alerts_per_1000_cycles"] is None
    assert (
        engine_bootstrap(eligible, [0.9, 0.8], 0.85, seed=1, repetitions=10, endpoint_only=True)[
            "percentile_intervals"
        ]["false_alerts_per_1000_cycles"]
        is None
    )


def test_final_outcomes_read_once_and_changed_selection_rejected(tmp_path):
    receipt = tmp_path / "receipt.json"
    calls = []

    def compute():
        calls.append(1)
        return dict(metrics={"measured": 0.5})

    first = once(receipt, "frozen", compute)
    assert once(receipt, "frozen", compute) == first and len(calls) == 1
    with pytest.raises(ValueError, match="different frozen"):
        once(receipt, "retuned", compute)
    assert len(calls) == 1


def test_interrupted_final_receipt_cannot_silently_reread_outcomes(tmp_path):
    receipt = tmp_path / "receipt.json"

    def fail():
        raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError):
        once(receipt, "frozen", fail)
    with pytest.raises(ValueError, match="Incomplete"):
        once(receipt, "frozen", lambda: {})
