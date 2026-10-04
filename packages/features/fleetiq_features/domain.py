"""Installation counters from bitemporal evidence, never inferred calendar usage."""

import math
from dataclasses import dataclass
from datetime import datetime

from fleetiq_features.schema import clock


@dataclass(frozen=True)
class TechnicalRecord:
    installation_id: str
    kind: str
    occurred_at: datetime
    recorded_at: datetime
    hours: float | None = None
    cycles: float | None = None
    component_age_hours: float | None = None


def visible_records(records, *, installation_id, as_of):
    end = clock(as_of, "synthetic_engine_demo")
    visible = []
    for row in records:
        occurred = clock(row.occurred_at, "synthetic_engine_demo")
        known = clock(row.recorded_at, "synthetic_engine_demo")
        if row.installation_id != installation_id or occurred > end or known > end:
            continue
        if known < occurred or row.kind not in {"installed", "usage", "maintenance", "removed"}:
            raise ValueError("Invalid technical record chronology or kind")
        for value in (row.hours, row.cycles, row.component_age_hours):
            if value is not None and (
                isinstance(value, bool) or not math.isfinite(value) or value < 0
            ):
                raise ValueError("Counters must be finite and nonnegative")
        visible.append(row)
    return sorted(set(visible), key=lambda row: (row.occurred_at, row.recorded_at, row.kind))


def domain_features(records, *, installation_id, as_of):
    rows = visible_records(records, installation_id=installation_id, as_of=as_of)
    installed = [row for row in rows if row.kind == "installed"]
    result = dict.fromkeys(
        ["hours_since_installation", "cycles_since_installation", "known_component_age_hours"]
    )
    result["maintenance_count"] = 0
    if len(installed) > 1:
        raise ValueError("Conflicting installation origins")
    if not installed:
        return result | {"installation_missing": 1}
    origin = installed[0]
    rows = [row for row in rows if row.occurred_at >= origin.occurred_at]
    if any(row.kind == "removed" for row in rows):
        return result | {"installation_missing": 1}
    usage = [row for row in rows if row.kind == "usage"]
    if usage:
        latest_at = max(row.occurred_at for row in usage)
        latest = [row for row in usage if row.occurred_at == latest_at]
        if len(latest) != 1:
            raise ValueError("Resolve revised/conflicting counters before extraction")
        # Usage records contain cumulative counters for this installation, not lifetime sums.
        for field in ("hours", "cycles"):
            values = [getattr(row, field) for row in usage if getattr(row, field) is not None]
            if values != sorted(values):
                raise ValueError("Usage counter reset requires a new installation identity")
        result["hours_since_installation"] = latest[0].hours
        result["cycles_since_installation"] = latest[0].cycles
        if origin.component_age_hours is not None and latest[0].hours is not None:
            result["known_component_age_hours"] = origin.component_age_hours + latest[0].hours
    result["maintenance_count"] = sum(row.kind == "maintenance" for row in rows)
    return result | {"installation_missing": 0}
