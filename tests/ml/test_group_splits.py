from copy import deepcopy

import pandas as pd
import pytest
from fleetiq_evaluation.splits import (
    assert_disjoint,
    benchmark_splits,
    load_config,
    qualified_engine,
)


def observations():
    return pd.DataFrame(
        [
            dict(
                unit_id=unit,
                cycle=cycle,
                **{f"setting_{i}": 0 for i in range(1, 4)},
                **{f"sensor_{i}": 7 for i in range(1, 22)},
            )
            for unit in range(1, 101)
            for cycle in (1, 2)
        ]
    )


def test_repeatable_whole_engine_splits_preserve_eda_and_qualified_ids():
    frame = observations()
    first = benchmark_splits(frame)
    assert first == benchmark_splits(frame.sample(frac=1, random_state=99))
    assert sum(first["engine_counts"].values()) == 100
    assert first["engine_counts"]["fit"] == 62
    assert set(first["eda_reviewed_fit_engines"]) == set(first["groups"]["fit"])
    assert qualified_engine(1, split="test") != qualified_engine(1)
    assert first["official_test_accessed"] is False


def test_overlap_targets_official_test_and_duplicate_serials_rejected():
    frame = observations()
    with pytest.raises(ValueError, match="leakage"):
        assert_disjoint({"fit": [qualified_engine(1)], "tune": [qualified_engine(1)]})
    with pytest.raises(ValueError, match="Official test"):
        assert_disjoint({"fit": [qualified_engine(1, split="test")]})
    with pytest.raises(ValueError, match="targets"):
        benchmark_splits(frame.assign(rul_cycles=50))
    config = deepcopy(load_config())
    config["benchmark"]["source_split"] = "test"
    with pytest.raises(ValueError, match="Official test"):
        benchmark_splits(frame, config)
    mapping = {qualified_engine(unit): "same-serial" for unit in range(1, 101)}
    with pytest.raises(ValueError, match="serial"):
        benchmark_splits(frame, serial_mapping=mapping)
    with pytest.raises(ValueError, match="Unique"):
        benchmark_splits(pd.concat([frame, frame.head(1)]))


def test_configurable_fractions_cannot_reassign_reviewed_eda_engines():
    config = deepcopy(load_config())
    config["benchmark"]["fractions"] = dict(fit=0.7, tune=0.15, calibration=0.15)
    changed = benchmark_splits(observations(), config)
    assert set(changed["eda_reviewed_fit_engines"]).issubset(changed["groups"]["fit"])
    config["benchmark"]["fractions"] = dict(fit=0.5, tune=0.25, calibration=0.25)
    with pytest.raises(ValueError, match="EDA"):
        benchmark_splits(observations(), config)
