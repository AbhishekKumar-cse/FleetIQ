"""Observed inputs only; labels and latent simulator truth live separately."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

from fleetiq_domain.conventions import UtcTimestamp

NASA_COLUMNS = ("unit_id", "cycle", *(f"setting_{i}" for i in range(1, 4)),
                *(f"sensor_{i}" for i in range(1, 22)))


class CmapssObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    source_kind: Literal["nasa_cmapss"] = "nasa_cmapss"
    schema_version: Literal["cmapss-v1"] = "cmapss-v1"
    life_unit: Literal["cycles"] = "cycles"
    subset: Literal["FD001", "FD002", "FD003", "FD004"]
    unit_id: int = Field(gt=0)
    cycle: int = Field(gt=0)
    settings: tuple[FiniteFloat, FiniteFloat, FiniteFloat]
    sensors: tuple[FiniteFloat, ...] = Field(min_length=21, max_length=21)
    installation_id: None = None
    measured_at: None = None
    transport_received_at: UtcTimestamp | None = None


class SyntheticObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    source_kind: Literal["synthetic_engine"] = "synthetic_engine"
    schema_version: Literal["synthetic-engine-v1"] = "synthetic-engine-v1"
    life_unit: Literal["operating_hours"] = "operating_hours"
    aircraft_id: UUID
    engine_serial: str = Field(min_length=1)
    component_serial: str = Field(min_length=1)
    installation_id: UUID
    measured_at: UtcTimestamp
    recorded_at: UtcTimestamp
    operating_hours: FiniteFloat = Field(ge=0)
    component_age_hours: FiniteFloat = Field(ge=0)
    flight_cycles: int = Field(ge=0)
    workload: FiniteFloat = Field(ge=0)
    ambient_temperature_c: FiniteFloat
    temperature_c: FiniteFloat
    oil_pressure_kpa: FiniteFloat = Field(ge=0)
    vibration_mm_s: FiniteFloat = Field(ge=0)
    temperature_unit: Literal["degC"] = "degC"
    pressure_unit: Literal["kPa"] = "kPa"
    vibration_unit: Literal["mm/s"] = "mm/s"
