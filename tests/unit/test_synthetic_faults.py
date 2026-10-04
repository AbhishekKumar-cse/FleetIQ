import pandas as pd
import pytest
from fleetiq_data.contracts import SyntheticObservation
from fleetiq_data.synthetic.degradation import generate_trajectory
from fleetiq_data.synthetic.failures import FailureParameters, sample_failures
from fleetiq_data.synthetic.quality import SensorFault, inject_quality
from pydantic import ValidationError


def parameters(base=0, damage=0):
    return FailureParameters(
        base_hazard_per_hour=base,
        damage_hazard_per_hour=damage,
        damage_exponent=2,
        confirmation_delay_hours=2,
    )


def test_confirmed_and_censored_events_are_deterministic():
    trajectory = generate_trajectory(hours=12)
    confirmed = sample_failures(trajectory.evaluator, parameters=parameters(base=1000))
    censored = sample_failures(trajectory.evaluator, parameters=parameters())
    assert confirmed.status.iloc[0] == "confirmed"
    assert pd.Timestamp(confirmed.event_time.iloc[0]) == pd.Timestamp(
        trajectory.observed.measured_at.iloc[1]
    )
    assert pd.Timestamp(confirmed.recorded_at.iloc[0]) > pd.Timestamp(confirmed.event_time.iloc[0])
    assert censored.status.iloc[0] == "censored" and censored.event_time.iloc[0] is None
    assert censored.followup_until.iloc[0] == trajectory.evaluator.measured_at.iloc[-1]
    pd.testing.assert_frame_equal(
        confirmed, sample_failures(trajectory.evaluator, parameters=parameters(base=1000))
    )


def test_damage_changes_physical_events_with_paired_failure_seed():
    healthy = generate_trajectory(hours=12, initial_damage=0)
    damaged = generate_trajectory(hours=12, initial_damage=100)
    model = parameters(damage=1)
    assert sample_failures(damaged.evaluator, parameters=model).status.iloc[0] == "confirmed"
    assert sample_failures(healthy.evaluator, parameters=model).status.iloc[0] == "censored"
    with pytest.raises(TypeError):
        sample_failures(healthy.evaluator, risk_score=0.99)


def test_quality_faults_do_not_change_physical_events_or_clean_inputs():
    trajectory = generate_trajectory(hours=24)
    original = trajectory.observed.copy(deep=True)
    events = sample_failures(trajectory.evaluator, parameters=parameters())
    quality = inject_quality(trajectory.observed, seed=7)
    pd.testing.assert_frame_equal(original, trajectory.observed)
    pd.testing.assert_frame_equal(
        events, sample_failures(trajectory.evaluator, parameters=parameters())
    )
    assert events.status.iloc[0] == "censored"
    assert set(quality.evaluator_faults.kind) == {
        "missing",
        "drift",
        "spike",
        "clock",
        "unit_mismatch",
    }
    assert set(quality.observed.columns) == set(original.columns)
    assert not {"latent_damage", "kind", "failure", "truth_access"}.intersection(
        quality.observed.columns
    )
    repeat = inject_quality(trajectory.observed, seed=7)
    pd.testing.assert_frame_equal(quality.observed, repeat.observed)
    pd.testing.assert_frame_equal(quality.evaluator_faults, repeat.evaluator_faults)


@pytest.mark.parametrize("kind", ["missing", "drift", "spike", "clock", "unit_mismatch"])
def test_explicit_fault_windows_and_quarantine_cases(kind):
    clean = generate_trajectory(hours=8).observed
    raw = inject_quality(
        clean, faults=[SensorFault(kind=kind, start=2, duration=2, magnitude=10)]
    ).observed
    pd.testing.assert_frame_equal(raw.iloc[:2], clean.iloc[:2])
    pd.testing.assert_frame_equal(raw.iloc[4:], clean.iloc[4:])
    if kind == "missing":
        assert raw.temperature_c.iloc[2:4].isna().all()
    elif kind == "drift":
        assert (raw.temperature_c - clean.temperature_c).iloc[2:4].tolist() == [10, 20]
    elif kind == "spike":
        assert (raw.temperature_c - clean.temperature_c).iloc[2:4].tolist() == [10, 10]
    elif kind == "clock":
        assert pd.Timestamp(raw.measured_at.iloc[2]) - pd.Timestamp(
            clean.measured_at.iloc[2]
        ) == pd.Timedelta(hours=10)
        assert raw.recorded_at.equals(clean.recorded_at)
    else:
        assert raw.temperature_unit.iloc[2:4].tolist() == ["degF", "degF"]
        assert raw.temperature_c.iloc[2] == clean.temperature_c.iloc[2] * 1.8 + 32
    if kind in {"missing", "unit_mismatch"}:
        with pytest.raises(ValidationError):
            SyntheticObservation.model_validate(raw.iloc[2].to_dict())


@pytest.mark.parametrize("regime", ["normal", "degrading", "ood"])
def test_all_regimes_support_independent_failures_and_quality(regime):
    trajectory = generate_trajectory(hours=16, regime=regime)
    events = sample_failures(trajectory.evaluator)
    quality = inject_quality(trajectory.observed)
    assert len(events) == 1 and len(quality.observed) == 16
    assert events.truth_access.iloc[0] == "evaluator_only"


def test_invalid_fault_windows_and_nonhourly_failure_truth():
    trajectory = generate_trajectory(hours=8)
    with pytest.raises(ValueError, match="window"):
        inject_quality(
            trajectory.observed, faults=[SensorFault(kind="missing", start=7, duration=2)]
        )
    with pytest.raises(ValueError, match="hourly"):
        sample_failures(trajectory.evaluator.iloc[::2])
    invalid = trajectory.evaluator.copy()
    invalid.loc[0, "latent_damage"] = -1
    with pytest.raises(ValueError, match="damage"):
        sample_failures(invalid)
    with pytest.raises(ValidationError):
        SensorFault(kind="missing", start=-1)
