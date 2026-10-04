"""Evaluator labels, explicitly separated from observed feature access."""

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd


def training_cycle_targets(observed: pd.DataFrame, horizon_cycles: int = 30) -> pd.DataFrame:
    if (
        isinstance(horizon_cycles, bool)
        or not isinstance(horizon_cycles, int)
        or horizon_cycles <= 0
    ):
        raise ValueError("horizon must be a positive integer number of cycles")
    keys = observed[["unit_id", "cycle"]].copy()
    if keys.empty or not np.isfinite(keys.to_numpy()).all():
        raise ValueError("training observations must contain finite keys")
    if ((keys <= 0) | (keys != np.floor(keys))).any().any():
        raise ValueError("unit and cycle must be positive integers")
    if keys.duplicated().any():
        raise ValueError("duplicate observation keys")
    terminal = keys.groupby("unit_id").cycle.transform("max")
    keys["as_of_cycle"] = keys.cycle
    keys["event_cycle"] = terminal
    keys["recorded_cycle"] = terminal
    keys["rul_cycles"] = terminal - keys.cycle
    keys["horizon_cycles"] = horizon_cycles
    keys["life_unit"] = "cycles"
    keys["eligible"] = keys.rul_cycles > 0
    keys["failure_within_horizon"] = (keys.rul_cycles <= horizon_cycles).astype("boolean")
    keys.loc[~keys.eligible, "failure_within_horizon"] = pd.NA
    keys["eligibility_reason"] = np.where(
        keys.eligible, "complete_run_to_failure", "already_failed"
    )
    return keys


@dataclass(frozen=True)
class HourTarget:
    as_of: datetime
    horizon_hours: int
    event_time: datetime | None
    recorded_time: datetime | None
    label_cutoff: datetime
    eligible: bool
    failure_within_horizon: bool | None
    reason: str
    life_unit: str = "operating_hours"


def synthetic_hour_target(
    *,
    as_of: datetime,
    label_cutoff: datetime,
    followup_until: datetime | None,
    followup_recorded_at: datetime | None,
    event_time: datetime | None = None,
    event_recorded_at: datetime | None = None,
    intervention_at: datetime | None = None,
    horizon_hours: int = 24,
) -> HourTarget:
    """Labels mature only when confirmation or uninterrupted follow-up is available."""
    if isinstance(horizon_hours, bool) or not isinstance(horizon_hours, int) or horizon_hours <= 0:
        raise ValueError("horizon must be positive integer hours")
    timestamps = [
        as_of,
        label_cutoff,
        followup_until,
        followup_recorded_at,
        event_time,
        event_recorded_at,
        intervention_at,
    ]
    if any(
        value is not None and (value.tzinfo is None or value.utcoffset() is None)
        for value in timestamps
    ):
        raise ValueError("timestamps must be timezone-aware")
    if (event_time is None) != (event_recorded_at is None):
        raise ValueError("event time and confirmation time must be supplied together")
    if event_time is not None and event_recorded_at < event_time:
        raise ValueError("confirmation cannot precede event")
    if (followup_until is None) != (followup_recorded_at is None):
        raise ValueError("follow-up and its recording time must be supplied together")
    if followup_until is not None and followup_recorded_at < followup_until:
        raise ValueError("follow-up recording cannot precede observation")
    end = as_of + timedelta(hours=horizon_hours)
    reason, label = "immature_followup", None
    if event_time is not None and event_time <= as_of:
        reason = "already_failed"
    elif label_cutoff < as_of:
        reason = "cutoff_before_observation"
    elif intervention_at is not None and intervention_at <= min(event_time or end, end):
        reason = "intervened_or_replaced"
    elif event_time is not None and event_time <= end:
        if event_recorded_at <= label_cutoff:
            reason, label = "confirmed_failure", True
        else:
            reason = "confirmation_unavailable_at_cutoff"
    elif (
        followup_until is not None
        and followup_until >= end
        and followup_recorded_at <= label_cutoff
        and label_cutoff >= end
    ):
        reason, label = "complete_followup", False
    return HourTarget(
        as_of,
        horizon_hours,
        event_time,
        event_recorded_at,
        label_cutoff,
        label is not None,
        label,
        reason,
    )
