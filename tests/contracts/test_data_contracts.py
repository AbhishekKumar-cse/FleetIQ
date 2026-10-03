from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fleetiq_data.contracts import CmapssObservation, SyntheticObservation
from pydantic import ValidationError


def nasa(**overrides):
    return CmapssObservation(
        **(
            {
                "subset": "FD001",
                "unit_id": 1,
                "cycle": 1,
                "settings": (0.0, 0.0, 100.0),
                "sensors": (1.0,) * 21,
            }
            | overrides
        )
    )


def synthetic(**overrides):
    return SyntheticObservation(
        **(
            {
                "aircraft_id": uuid4(),
                "engine_serial": "DEMO-E-1",
                "component_serial": "DEMO-C-1",
                "installation_id": uuid4(),
                "measured_at": datetime(2026, 1, 1, tzinfo=UTC),
                "recorded_at": datetime(2026, 1, 1, tzinfo=UTC),
                "operating_hours": 1.0,
                "component_age_hours": 1.0,
                "flight_cycles": 1,
                "workload": 1.0,
                "ambient_temperature_c": 25.0,
                "temperature_c": 90.0,
                "oil_pressure_kpa": 300.0,
                "vibration_mm_s": 1.0,
            }
            | overrides
        )
    )


def test_tracks_have_distinct_units_and_metadata():
    assert nasa().life_unit == "cycles" and nasa().measured_at is None
    assert synthetic().life_unit == "operating_hours"


@pytest.mark.parametrize(
    "override",
    [
        {"life_unit": "operating_hours"},
        {"sensors": (1.0,) * 26},
        {"vibration_mm_s": 1},
        {"rul": 30},
        {"measured_at": "2026-01-01T00:00:00Z"},
    ],
)
def test_nasa_does_not_accept_synthetic_units_or_unobserved_values(override):
    with pytest.raises(ValidationError):
        nasa(**override)


@pytest.mark.parametrize(
    "override",
    [
        {"life_unit": "cycles"},
        {"pressure_unit": "psi"},
        {"failure_at": "2026-01-02"},
        {"latent_damage": 0.8},
        {"temperature_c": float("nan")},
    ],
)
def test_synthetic_rejects_truth_unit_mixing_and_nonfinite_values(override):
    with pytest.raises(ValidationError):
        synthetic(**override)
