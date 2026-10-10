import numpy as np
import pandas as pd
import pytest
from fleetiq_data.contracts import NASA_COLUMNS
from fleetiq_evaluation.classification import final_labels
from fleetiq_evaluation.rul import nasa_score


def test_asymmetric_score_sign_exact_zero_and_overflow():
    assert nasa_score([20], [20])["total"] == 0
    assert np.isclose(nasa_score([30], [20])["total"], np.expm1(1))
    assert np.isclose(nasa_score([7], [20])["total"], np.expm1(1))
    assert nasa_score([30], [20])["total"] > nasa_score([10], [20])["total"]
    assert nasa_score([10000], [0])["overflow"]
    with pytest.raises(ValueError, match="Finite"):
        nasa_score([np.nan], [0])


def test_endpoint_count_and_target_mapping_includes_short_engines():
    observed = pd.DataFrame(
        [[1, 1, *([0] * 24)], [1, 5, *([0] * 24)], [2, 30, *([0] * 24)]], columns=NASA_COLUMNS
    )
    endpoints = final_labels(observed, [10, 20])
    assert endpoints.cycle.tolist() == [5, 30]
    assert endpoints.rul_cycles.tolist() == [10, 20]
    assert len(endpoints) == 2  # The evaluator must explicitly record short-history exclusions.
    with pytest.raises(ValueError, match="mapping"):
        final_labels(observed, [10])
