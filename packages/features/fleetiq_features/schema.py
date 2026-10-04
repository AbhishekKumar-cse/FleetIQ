"""One deterministic clock/eligibility contract shared by all causal feature versions."""

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from numbers import Real
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CONFIG = json.loads((ROOT / "config/features_v1.json").read_text())
STATISTICS = tuple(CONFIG["statistics"])
QUALITIES = frozenset({"valid", "flagged", "missing", "invalid"})


@dataclass(frozen=True)
class Sample:
    at: datetime | int
    value: float | None
    recorded_at: datetime | int
    quality: str = "valid"
    imputed: bool = False
    stream: str | None = None


def track_schema(track):
    if track not in CONFIG["tracks"]:
        raise ValueError("Explicit supported feature track required")
    return CONFIG["tracks"][track]


def clock(value, track, *, boundary=False):
    kind = track_schema(track)["clock"]
    if kind == "utc":
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Synthetic features require aware UTC-compatible timestamps")
        return value.astimezone(UTC)
    if type(value) is not int or (not boundary and value < 1):
        raise ValueError("NASA features require native positive integer cycles")
    return value


def eligible(sample):
    return (
        sample.quality in CONFIG["eligible_quality"]
        and not sample.imputed
        and isinstance(sample.value, Real)
        and not isinstance(sample.value, bool)
        and math.isfinite(sample.value)
    )


def select_window(samples, *, track, as_of, window_start, source_cutoff=None):
    end = clock(as_of, track)
    start = clock(window_start, track, boundary=True)
    cutoff = end if source_cutoff is None else clock(source_cutoff, track)
    if start >= end:
        raise ValueError("A positive causal window is required")
    selected = []
    for sample in samples:
        at, known = clock(sample.at, track), clock(sample.recorded_at, track)
        # Excluded future evidence cannot change diagnostics, masks, or earlier vectors.
        if not start < at <= end or known > cutoff:
            continue
        if known < at:
            raise ValueError("Recording cannot precede the observation")
        if sample.quality not in QUALITIES or type(sample.imputed) is not bool:
            raise ValueError("Unknown quality or imputation mask")
        selected.append(sample)
    selected.sort(key=lambda row: clock(row.at, track))
    identities = {row.stream for row in selected}
    if len(identities) > 1:
        raise ValueError("Feature windows cannot cross engine/installation streams")
    # One canonical sample per timestamp: conflicts must be resolved before feature extraction.
    unique = {}
    for row in selected:
        at = clock(row.at, track)
        if at in unique and unique[at] != row:
            raise ValueError("Conflicting observations at the same timestamp")
        unique[at] = row
    return list(unique.values())


def feature_names(track):
    return tuple(
        f"{channel}.{stat}" for channel in track_schema(track)["channels"] for stat in STATISTICS
    )


def validate_channels(channels, track):
    if set(channels) - set(track_schema(track)["channels"]):
        raise ValueError("Unknown channel; labels/context are not sensor features")


def masks(rows):
    return {
        "observed": [eligible(row) for row in rows],
        "imputed": [row.imputed for row in rows],
        "quality": [row.quality for row in rows],
    }
