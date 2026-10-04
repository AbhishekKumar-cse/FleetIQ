from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from fleetiq_data.contracts import SyntheticObservation
from fleetiq_data.synthetic.degradation import generate_trajectory


def test_deterministic_prefix_causality_and_observation_contract():
    short = generate_trajectory(seed=42, hours=24)
    repeat = generate_trajectory(seed=42, hours=24)
    long = generate_trajectory(seed=42, hours=48)
    pd.testing.assert_frame_equal(short.observed, repeat.observed)
    pd.testing.assert_frame_equal(short.evaluator, repeat.evaluator)
    pd.testing.assert_frame_equal(short.observed, long.observed.iloc[:24].reset_index(drop=True))
    pd.testing.assert_frame_equal(short.evaluator, long.evaluator.iloc[:24].reset_index(drop=True))
    assert short.observed.to_json() == repeat.observed.to_json()
    for record in short.observed.to_dict("records"):
        SyntheticObservation.model_validate(record)
    assert not {"latent_damage", "failure", "rul", "regime"}.intersection(short.observed.columns)
    assert short.evaluator.latent_damage.is_monotonic_increasing


def test_workload_and_age_increase_damage_with_paired_randomness():
    baseline = generate_trajectory(seed=3, hours=72)
    loaded = generate_trajectory(seed=3, hours=72, workload_scale=1.5)
    aged = generate_trajectory(seed=3, hours=72, initial_age_hours=1000)
    assert loaded.evaluator.latent_damage.iloc[-1] > baseline.evaluator.latent_damage.iloc[-1]
    assert aged.evaluator.latent_damage.iloc[-1] > baseline.evaluator.latent_damage.iloc[-1]
    assert loaded.observed.ambient_temperature_c.equals(baseline.observed.ambient_temperature_c)


def test_damage_drives_health_channels_without_changing_context():
    healthy = generate_trajectory(hours=8)
    damaged = generate_trajectory(hours=8, initial_damage=0.8)
    assert healthy.observed.workload.equals(damaged.observed.workload)
    assert (damaged.observed.temperature_c > healthy.observed.temperature_c).all()
    assert (damaged.observed.oil_pressure_kpa < healthy.observed.oil_pressure_kpa).all()
    assert (damaged.observed.vibration_mm_s > healthy.observed.vibration_mm_s).all()


def test_replacement_has_distinct_identity_and_resets_component_age():
    original = generate_trajectory(hours=8, initial_age_hours=500)
    replacement = generate_trajectory(
        hours=8,
        component_serial="FICTIONAL-REPLACEMENT",
        installed_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=8),
    )
    assert original.observed.aircraft_id.iloc[0] == replacement.observed.aircraft_id.iloc[0]
    assert original.observed.installation_id.iloc[0] != replacement.observed.installation_id.iloc[0]
    assert replacement.observed.component_age_hours.iloc[0] == 0
    assert replacement.evaluator.latent_damage.iloc[0] == 0
    assert replacement.installations.fictional.all()


def test_disclosed_regimes_are_distinct():
    normal = generate_trajectory(hours=48)
    degrading = generate_trajectory(hours=48, regime="degrading")
    ood = generate_trajectory(hours=48, regime="ood")
    assert degrading.evaluator.latent_damage.iloc[-1] > normal.evaluator.latent_damage.iloc[-1]
    assert (ood.observed.ambient_temperature_c > normal.observed.ambient_temperature_c).all()


@pytest.mark.parametrize(
    "changes",
    [
        {"seed": -1},
        {"hours": 0},
        {"initial_damage": -1},
        {"initial_age_hours": float("nan")},
        {"workload_scale": 0},
        {"regime": "unknown"},
        {"installed_at": datetime(2026, 1, 1)},
    ],
)
def test_invalid_simulation_inputs(changes):
    with pytest.raises(ValueError):
        generate_trajectory(**changes)
