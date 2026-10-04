"""Duration-based coverage of observed evidence; fills do not hide missingness."""

from dataclasses import dataclass
from datetime import timedelta
from math import isfinite

from fleetiq_data.quality.time import utc


@dataclass(frozen=True)
class Coverage:
    coverage: float
    max_gap_seconds: float
    supported: bool
    observed_seconds: float


def coverage(samples, *, start, as_of, cadence_seconds, max_gap_seconds, minimum_coverage=0.95):
    start, end = utc(start), utc(as_of)
    if (
        end <= start
        or not isfinite(cadence_seconds)
        or cadence_seconds <= 0
        or not isfinite(max_gap_seconds)
        or max_gap_seconds < 0
        or not 0 <= minimum_coverage <= 1
    ):
        raise ValueError("Invalid coverage window")
    points = {}
    for timestamp, value in samples:
        timestamp = utc(timestamp)
        if timestamp > end:
            continue
        if timestamp in points and points[timestamp] != value:
            raise ValueError("Conflicting sample at same time")
        points[timestamp] = value
    ordered = sorted(points.items())
    intervals = []
    for i, (at, value) in enumerate(ordered):
        if value is None or not isfinite(value):
            continue
        next_at = ordered[i + 1][0] if i + 1 < len(ordered) else end
        a = max(start, at)
        b = min(end, next_at, at + timedelta(seconds=cadence_seconds))
        if b > a:
            intervals.append((a, b))
    cursor, observed, longest = start, 0.0, 0.0
    for a, b in intervals:
        longest = max(longest, (a - cursor).total_seconds())
        observed += max(0, (b - max(a, cursor)).total_seconds())
        cursor = max(cursor, b)
    longest = max(longest, (end - cursor).total_seconds())
    fraction = observed / (end - start).total_seconds()
    return Coverage(
        fraction, longest, fraction >= minimum_coverage and longest <= max_gap_seconds, observed
    )


def transition_flags(previous_value, value, previous_usage, usage, *, spike_delta):
    """Usage rollback differs from plausible sensor spikes; neither clips evidence."""
    flags = []
    if previous_usage is not None and usage is not None and usage < previous_usage:
        flags.append("usage_rollback")
    if (
        previous_value is not None
        and value is not None
        and abs(value - previous_value) > spike_delta
    ):
        flags.append("sensor_spike")
    return tuple(flags)
