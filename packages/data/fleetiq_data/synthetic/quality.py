"""Measurement corruption never changes the physical failure process."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

import numpy as np
import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

from fleetiq_data.fetch import ROOT
from fleetiq_data.synthetic.workload import stream

CHANNELS = ("temperature_c", "oil_pressure_kpa", "vibration_mm_s")
FAULT_COLUMNS = (
    "installation_id",
    "component_serial",
    "canonical_measured_at",
    "row_position",
    "kind",
    "channel",
    "magnitude",
    "truth_access",
)


class SensorFault(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["missing", "drift", "spike", "clock", "unit_mismatch"]
    start: int = Field(ge=0)
    duration: int = Field(default=1, ge=1)
    channel: Literal["temperature_c", "oil_pressure_kpa", "vibration_mm_s"] = "temperature_c"
    magnitude: FiniteFloat = 25


@dataclass(frozen=True)
class QualityResult:
    observed: pd.DataFrame
    evaluator_faults: pd.DataFrame


def inject_quality(
    observed: pd.DataFrame, *, seed: int = 26249, faults: Sequence[SensorFault] | None = None
) -> QualityResult:
    """Return raw measurements, including intentionally invalid quarantine cases.

    Explicit plans use installation-relative row positions. With no plan, five
    independently seeded windows are drawn per installation; overlaps are allowed.
    """
    if observed.empty:
        raise ValueError("empty observed trajectory")
    clean = observed.reset_index(drop=True)
    raw = clean.copy(deep=True)
    config = yaml.safe_load((ROOT / "config" / "synthetic.yaml").read_text())["quality"]
    truth = []
    for installation, group in clean.groupby("installation_id", sort=True):
        rng = stream(seed, str(installation), "measurement_faults")
        plans = faults
        if plans is None:
            duration = min(len(group), config["default_window_hours"])
            plans = [
                SensorFault(
                    kind=kind,
                    start=int(rng.integers(0, len(group) - duration + 1)),
                    duration=duration,
                    channel=CHANNELS[int(rng.integers(0, len(CHANNELS)))],
                    magnitude={
                        "drift": config["drift_per_hour"],
                        "clock": config["clock_offset_hours"],
                    }.get(kind, config["spike_magnitude"]),
                )
                for kind in config["fault_kinds"]
            ]
        for plan in plans:
            if plan.start + plan.duration > len(group):
                raise ValueError("fault window exceeds installation observations")
            for offset in range(plan.duration):
                index = group.index[plan.start + offset]
                channel = plan.channel
                if plan.kind == "missing":
                    raw.at[index, channel] = np.nan
                elif plan.kind in {"drift", "spike"}:
                    raw.at[index, channel] += plan.magnitude * (
                        offset + 1 if plan.kind == "drift" else 1
                    )
                elif plan.kind == "clock":
                    measured = pd.Timestamp(clean.at[index, "measured_at"])
                    raw.at[index, "measured_at"] = (
                        measured + timedelta(hours=plan.magnitude)
                    ).isoformat()
                    channel = "measured_at"
                else:
                    unit, factor, bias, incorrect = {
                        "temperature_c": ("temperature_unit", 1.8, 32, "degF"),
                        "oil_pressure_kpa": ("pressure_unit", 0.1450377377, 0, "psi"),
                        "vibration_mm_s": ("vibration_unit", 0.0393700787, 0, "in/s"),
                    }[channel]
                    raw.at[index, channel] = clean.at[index, channel] * factor + bias
                    raw.at[index, unit] = incorrect
                truth.append(
                    {
                        "installation_id": installation,
                        "component_serial": clean.at[index, "component_serial"],
                        "canonical_measured_at": clean.at[index, "measured_at"],
                        "row_position": plan.start + offset,
                        "kind": plan.kind,
                        "channel": channel,
                        "magnitude": plan.magnitude,
                        "truth_access": "evaluator_only",
                    }
                )
    return QualityResult(raw, pd.DataFrame(truth, columns=FAULT_COLUMNS))
