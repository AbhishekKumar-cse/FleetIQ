"""Independent physical-event sampling from evaluator-only latent damage."""

from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

import numpy as np
import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

from fleetiq_data.fetch import ROOT
from fleetiq_data.synthetic.workload import stream


class FailureParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_hazard_per_hour: FiniteFloat = Field(ge=0)
    damage_hazard_per_hour: FiniteFloat = Field(ge=0)
    damage_exponent: FiniteFloat = Field(gt=0)
    confirmation_delay_hours: FiniteFloat = Field(ge=0)


def sample_failures(
    evaluator: pd.DataFrame, *, seed: int = 26249, parameters: FailureParameters | None = None
) -> pd.DataFrame:
    """First failure per installation; no classifier scores enter this function.

    Each sample is a one-hour interval ending at the truth row's timestamp.
    The installation's initial row is a baseline, not an exposure interval.
    Confirmation may fall beyond the simulation cutoff and remains evaluator truth.
    """
    if evaluator.empty:
        raise ValueError("empty evaluator trajectory")
    parameters = parameters or FailureParameters.model_validate(
        yaml.safe_load((ROOT / "config" / "synthetic.yaml").read_text())["failures"]
    )
    events = []
    for installation, group in evaluator.groupby("installation_id", sort=True):
        if group.component_serial.nunique() != 1:
            raise ValueError("installation must contain exactly one component")
        serial = group.component_serial.iloc[0]
        timestamps = pd.to_datetime(group.measured_at, utc=True, errors="raise")
        if any(pd.Timestamp(value).tzinfo is None for value in group.measured_at):
            raise ValueError("truth timestamps must be timezone-aware")
        if timestamps.isna().any() or not timestamps.is_monotonic_increasing:
            raise ValueError("truth timestamps must be present and monotonic")
        if not (timestamps.diff().dropna() == pd.Timedelta(hours=1)).all():
            raise ValueError("failure process requires consecutive hourly exposures")
        damage = group.latent_damage.to_numpy(dtype=float)
        if not np.isfinite(damage).all() or (damage < 0).any():
            raise ValueError("latent damage must be finite and nonnegative")
        rng = stream(seed, f"{serial}:{installation}", "physical_failure")
        event_time = None
        for timestamp, value in zip(timestamps.iloc[1:], damage[1:], strict=True):
            hazard = parameters.base_hazard_per_hour
            hazard += parameters.damage_hazard_per_hour * float(value) ** parameters.damage_exponent
            if not np.isfinite(hazard):
                raise ValueError("nonfinite physical hazard")
            if rng.random() < -np.expm1(-hazard):
                event_time = timestamp.to_pydatetime()
                break
        end = timestamps.iloc[-1].to_pydatetime()
        recorded = (
            event_time + timedelta(hours=parameters.confirmation_delay_hours) if event_time else end
        )
        events.append(
            {
                "event_id": str(uuid5(NAMESPACE_URL, f"fleetiq:failure:{seed}:{installation}")),
                "installation_id": installation,
                "component_serial": serial,
                "source_kind": "synthetic_engine",
                "status": "confirmed" if event_time else "censored",
                "event_time": event_time.isoformat() if event_time else None,
                "recorded_at": recorded.isoformat(),
                "followup_until": (event_time or end).isoformat(),
                "followup_recorded_at": recorded.isoformat(),
                "truth_access": "evaluator_only",
            }
        )
    return pd.DataFrame(events)
