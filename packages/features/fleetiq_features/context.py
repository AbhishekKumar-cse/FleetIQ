"""Frozen train-only numeric context encoding and regime-specific sensor residuals."""

import hashlib
import json
import math
from dataclasses import dataclass

from fleetiq_features.schema import clock


@dataclass(frozen=True)
class ContextRecord:
    stream: str
    occurred_at: object
    recorded_at: object
    workload: float | None = None
    ambient_temperature_c: float | None = None
    regime: str | None = None


@dataclass(frozen=True)
class ContextFit:
    track: str
    groups: tuple[str, ...]
    centers: tuple[float | None, float | None]
    categories: tuple[str, ...]
    baselines: tuple[tuple[str, str, float], ...]
    content_hash: str


def fit_context(rows, *, track, partition, training_groups):
    if partition != "train" or not training_groups:
        raise ValueError("Context fitting requires explicit training membership")
    groups = tuple(sorted(set(map(str, training_groups))))
    rows = list(rows)
    if not rows or any(str(row["group"]) not in groups for row in rows):
        raise ValueError("Nontraining context encountered")
    centers = []
    for field in ("workload", "ambient_temperature_c"):
        values = [float(row[field]) for row in rows if row.get(field) is not None]
        if any(not math.isfinite(value) for value in values):
            raise ValueError("Finite context required")
        centers.append(sum(values) / len(values) if values else None)
    categories = tuple(
        sorted({str(row["regime"]) for row in rows if row.get("regime") is not None})
    )
    buckets = {}
    for row in rows:
        for channel, value in row.get("sensors", {}).items():
            if value is not None and math.isfinite(value):
                buckets.setdefault((str(row.get("regime")), channel), []).append(float(value))
    baselines = tuple(
        sorted((regime, channel, sum(v) / len(v)) for (regime, channel), v in buckets.items())
    )
    body = dict(
        track=track, groups=groups, centers=centers, categories=categories, baselines=baselines
    )
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return ContextFit(track, groups, tuple(centers), categories, baselines, digest)


def context_features(records, *, stream, as_of, fit, sensors=None):
    end = clock(as_of, fit.track)
    rows = [
        row
        for row in records
        if row.stream == stream
        and clock(row.occurred_at, fit.track) <= end
        and clock(row.recorded_at, fit.track) <= end
    ]
    if any(clock(row.recorded_at, fit.track) < clock(row.occurred_at, fit.track) for row in rows):
        raise ValueError("Context recording cannot precede occurrence")
    rows.sort(key=lambda row: (row.occurred_at, row.recorded_at))
    if len(rows) > 1 and rows[-1].occurred_at == rows[-2].occurred_at:
        raise ValueError("Resolve conflicting context before extraction")
    latest = rows[-1] if rows else None
    result = {}
    for field, center in zip(("workload", "ambient_temperature_c"), fit.centers, strict=True):
        value = getattr(latest, field) if latest else None
        if value is not None and (isinstance(value, bool) or not math.isfinite(value)):
            raise ValueError("Finite context required")
        result[f"context.{field}_centered"] = (
            value - center if value is not None and center is not None else None
        )
        result[f"context.{field}_missing"] = int(value is None)
    regime = latest.regime if latest else None
    for category in fit.categories:
        result[f"context.regime.{category}"] = int(regime == category)
    result["context.regime_unknown"] = int(regime not in fit.categories)
    for channel, value in sorted((sensors or {}).items()):
        baseline = next(
            (
                mean
                for category, name, mean in fit.baselines
                if category == regime and name == channel
            ),
            None,
        )
        result[f"context.{channel}_residual"] = (
            value - baseline if value is not None and baseline is not None else None
        )
    return result
