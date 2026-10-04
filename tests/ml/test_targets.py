from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from fleetiq_data.targets import synthetic_hour_target, training_cycle_targets

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_native_rul_horizon_boundary_terminal_and_engine_isolation():
    observed = pd.DataFrame({"unit_id": [1, 1, 1, 2, 2], "cycle": [4, 5, 35, 1, 5]})
    labels = training_cycle_targets(observed)
    assert labels.rul_cycles.tolist() == [31, 30, 0, 4, 0]
    assert labels.failure_within_horizon.iloc[:2].tolist() == [False, True]
    assert not labels.eligible.iloc[2]
    assert pd.isna(labels.failure_within_horizon.iloc[2])
    assert observed.columns.tolist() == ["unit_id", "cycle"]
    assert (labels.life_unit == "cycles").all()


def hour_target(**changes):
    inputs = dict(
        as_of=NOW,
        label_cutoff=NOW + timedelta(hours=48),
        followup_until=NOW + timedelta(hours=24),
        followup_recorded_at=NOW + timedelta(hours=25),
    )
    return synthetic_hour_target(**(inputs | changes))


def test_mature_negative_and_confirmed_boundary_positive():
    negative = hour_target()
    assert negative.eligible and negative.failure_within_horizon is False
    positive = hour_target(
        event_time=NOW + timedelta(hours=24), event_recorded_at=NOW + timedelta(hours=25)
    )
    assert positive.eligible and positive.failure_within_horizon is True
    assert positive.life_unit == "operating_hours"


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"followup_until": None, "followup_recorded_at": None}, "immature_followup"),
        ({"followup_until": NOW + timedelta(hours=23)}, "immature_followup"),
        ({"intervention_at": NOW + timedelta(hours=12)}, "intervened_or_replaced"),
        ({"event_time": NOW, "event_recorded_at": NOW}, "already_failed"),
        (
            {
                "event_time": NOW + timedelta(hours=2),
                "event_recorded_at": NOW + timedelta(hours=49),
            },
            "confirmation_unavailable_at_cutoff",
        ),
        ({"followup_recorded_at": NOW + timedelta(hours=49)}, "immature_followup"),
    ],
)
def test_censoring_and_delayed_evidence_are_not_negative(changes, reason):
    target = hour_target(**changes)
    assert not target.eligible and target.failure_within_horizon is None
    assert target.reason == reason


@pytest.mark.parametrize(
    "changes",
    [
        {"as_of": NOW.replace(tzinfo=None)},
        {"event_time": NOW},
        {"horizon_hours": 0},
        {"followup_recorded_at": NOW},
    ],
)
def test_invalid_hour_contracts(changes):
    with pytest.raises(ValueError):
        hour_target(**changes)


def test_cycle_targets_cannot_be_called_with_hour_horizon():
    with pytest.raises(TypeError):
        training_cycle_targets(pd.DataFrame(), horizon_hours=24)
