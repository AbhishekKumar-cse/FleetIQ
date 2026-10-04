"""Age/context drive latent damage; measurements expose no simulator truth."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

import numpy as np
import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

from fleetiq_data.contracts import SyntheticObservation
from fleetiq_data.fetch import ROOT
from fleetiq_data.synthetic.workload import hourly_context, stream


class Coefficients(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_damage_per_hour: FiniteFloat = Field(gt=0)
    age_coefficient_per_hour: FiniteFloat = Field(ge=0)
    workload_exponent: FiniteFloat = Field(gt=0)
    heat_coefficient_per_c: FiniteFloat = Field(ge=0)
    damage_noise_fraction: FiniteFloat = Field(ge=0, lt=1)


@dataclass(frozen=True)
class Trajectory:
    observed: pd.DataFrame
    evaluator: pd.DataFrame
    installations: pd.DataFrame


def generate_trajectory(
    *,
    seed: int = 26249,
    hours: int = 72,
    component_serial: str = "FICTIONAL-ENG-001",
    installed_at: datetime = datetime(2026, 1, 1, tzinfo=UTC),
    aircraft_id: UUID | None = None,
    initial_age_hours: float = 0,
    initial_damage: float = 0,
    workload_scale: float = 1,
    regime: Literal["normal", "degrading", "ood"] = "normal",
) -> Trajectory:
    if isinstance(hours, bool) or not isinstance(hours, int) or hours < 1:
        raise ValueError("hours must be a positive integer")
    if not component_serial or installed_at.tzinfo is None or installed_at.utcoffset() is None:
        raise ValueError("serial and timezone-aware installation time are required")
    if not np.isfinite([initial_age_hours, initial_damage, workload_scale]).all():
        raise ValueError("simulation inputs must be finite")
    if initial_age_hours < 0 or initial_damage < 0 or workload_scale <= 0:
        raise ValueError("age/damage must be nonnegative and workload positive")
    config = yaml.safe_load((ROOT / "config" / "synthetic.yaml").read_text())
    if regime not in config["regimes"]:
        raise ValueError("unknown regime")
    coefficients = Coefficients.model_validate(config["degradation"])
    context = config["regimes"][regime]
    measurement = config["measurement"]
    installed_at = installed_at.astimezone(UTC)
    aircraft = aircraft_id or uuid5(NAMESPACE_URL, f"fleetiq:fictional-aircraft:{seed}")
    installation = uuid5(
        NAMESPACE_URL, f"fleetiq:{seed}:{component_serial}:{installed_at.isoformat()}"
    )
    rngs = {
        name: stream(seed, component_serial, name)
        for name in ("workload", "environment", "damage", "measurement")
    }
    observed, truth = [], []
    damage = initial_damage
    previous_workload, previous_ambient = 0.0, 25.0
    for hour in range(hours):
        age = initial_age_hours + hour
        if hour:
            increment = coefficients.base_damage_per_hour * context["damage_multiplier"]
            increment *= 1 + coefficients.age_coefficient_per_hour * (age - 1)
            increment *= previous_workload**coefficients.workload_exponent
            increment *= 1 + coefficients.heat_coefficient_per_c * max(previous_ambient - 25, 0)
            increment *= float(
                rngs["damage"].uniform(
                    1 - coefficients.damage_noise_fraction, 1 + coefficients.damage_noise_fraction
                )
            )
            damage += increment
        workload, ambient = hourly_context(
            hour,
            rngs["workload"],
            rngs["environment"],
            workload_scale=workload_scale * context["workload_multiplier"],
            ambient_offset_c=context["ambient_offset_c"],
        )
        noise = rngs["measurement"].normal(size=3)
        temperature = (
            measurement["temperature_base_c"]
            + 0.3 * (ambient - 25)
            + measurement["temperature_workload_c"] * workload
            + measurement["temperature_damage_c"] * damage
            + measurement["temperature_noise_c"] * noise[0]
        )
        pressure = max(
            0,
            measurement["pressure_base_kpa"]
            - measurement["pressure_damage_kpa"] * damage
            + measurement["pressure_workload_kpa"] * workload
            + measurement["pressure_noise_kpa"] * noise[1],
        )
        vibration = max(
            0,
            measurement["vibration_base_mm_s"]
            + measurement["vibration_workload_mm_s"] * workload
            + measurement["vibration_damage_mm_s"] * damage
            + measurement["vibration_noise_mm_s"] * noise[2],
        )
        measured = installed_at + timedelta(hours=hour)
        observation = SyntheticObservation(
            aircraft_id=aircraft,
            engine_serial=component_serial,
            component_serial=component_serial,
            installation_id=installation,
            measured_at=measured,
            recorded_at=measured + timedelta(minutes=5),
            operating_hours=hour,
            component_age_hours=age,
            flight_cycles=hour,
            workload=workload,
            ambient_temperature_c=ambient,
            temperature_c=temperature,
            oil_pressure_kpa=pressure,
            vibration_mm_s=vibration,
        )
        observed.append(observation.model_dump(mode="json"))
        truth.append(
            {
                "installation_id": str(installation),
                "component_serial": component_serial,
                "measured_at": measured.isoformat(),
                "latent_damage": damage,
                "regime": regime,
                "source_kind": "synthetic_engine",
            }
        )
        previous_workload, previous_ambient = workload, ambient
    intervals = [
        {
            "installation_id": str(installation),
            "aircraft_id": str(aircraft),
            "component_serial": component_serial,
            "installed_at": installed_at.isoformat(),
            "removed_at": None,
            "initial_age_hours": initial_age_hours,
            "fictional": True,
        }
    ]
    return Trajectory(pd.DataFrame(observed), pd.DataFrame(truth), pd.DataFrame(intervals))
