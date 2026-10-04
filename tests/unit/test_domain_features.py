from datetime import UTC, datetime, timedelta

import pytest
from fleetiq_features.context import ContextRecord, context_features, fit_context
from fleetiq_features.domain import TechnicalRecord, domain_features

AT = datetime(2026, 1, 1, tzinfo=UTC)


def test_replacement_resets_counters_and_unknown_age_remains_unknown():
    records = [
        TechnicalRecord("old", "installed", AT, AT, component_age_hours=100),
        TechnicalRecord("old", "usage", AT, AT, hours=20, cycles=4),
        TechnicalRecord("new", "installed", AT, AT),
        TechnicalRecord("new", "usage", AT, AT, hours=2, cycles=1),
    ]
    old = domain_features(records, installation_id="old", as_of=AT)
    new = domain_features(records, installation_id="new", as_of=AT)
    assert old["known_component_age_hours"] == 120
    assert new["hours_since_installation"] == 2
    assert new["known_component_age_hours"] is None


def test_future_and_late_records_cannot_change_past_features():
    records = [TechnicalRecord("i", "installed", AT, AT, component_age_hours=0)]
    expected = domain_features(records, installation_id="i", as_of=AT)
    records += [
        TechnicalRecord("i", "maintenance", AT, AT + timedelta(days=1)),
        TechnicalRecord("i", "removed", AT + timedelta(days=1), AT + timedelta(days=1)),
    ]
    assert domain_features(records, installation_id="i", as_of=AT) == expected
    assert domain_features([], installation_id="i", as_of=AT)["installation_missing"] == 1


def test_frozen_train_context_residuals_missingness_and_unknown_categories():
    fit = fit_context(
        [
            dict(
                group="train",
                workload=2,
                ambient_temperature_c=20,
                regime="low",
                sensors={"temperature_c": 10},
            )
        ],
        track="synthetic_engine_demo",
        partition="train",
        training_groups=["train"],
    )
    records = [ContextRecord("i", AT, AT, 3, 25, "low")]
    result = context_features(records, stream="i", as_of=AT, fit=fit, sensors={"temperature_c": 14})
    assert result["context.workload_centered"] == 1
    assert result["context.temperature_c_residual"] == 4
    assert result["context.regime_unknown"] == 0
    records += [ContextRecord("i", AT, AT + timedelta(days=1), 100, 100, "new")]
    assert (
        context_features(records, stream="i", as_of=AT, fit=fit, sensors={"temperature_c": 14})
        == result
    )
    assert context_features([], stream="i", as_of=AT, fit=fit)["context.workload_missing"] == 1
    with pytest.raises(ValueError):
        fit_context(
            [dict(group="tune")],
            track="synthetic_engine_demo",
            partition="train",
            training_groups=["train"],
        )
    with pytest.raises(ValueError):
        fit_context([], track="synthetic_engine_demo", partition="test", training_groups=["test"])


def test_counter_resets_and_conflicting_installations_are_rejected():
    records = [
        TechnicalRecord("i", "installed", AT, AT),
        TechnicalRecord("i", "usage", AT, AT, hours=10),
        TechnicalRecord("i", "usage", AT + timedelta(hours=1), AT + timedelta(hours=1), hours=2),
    ]
    with pytest.raises(ValueError, match="reset"):
        domain_features(records, installation_id="i", as_of=AT + timedelta(hours=1))
