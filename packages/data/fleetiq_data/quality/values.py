"""Keep raw evidence and plausible anomalies; invalid channels are quarantined."""

import math
from dataclasses import dataclass

from fleetiq_data.quality.units import convert


@dataclass(frozen=True)
class QualityValue:
    raw_value: object
    raw_unit: str | None
    value: float | None
    canonical_unit: str
    quality: str
    reasons: tuple[str, ...] = ()


def validate_value(raw, unit, profile, *, pressure_kind=None):
    def result(value, quality, *reasons):
        return QualityValue(raw, unit, value, profile.unit, quality, reasons)

    if raw is None:
        return result(
            None, "missing", "essential_missing" if profile.essential else "optional_missing"
        )
    try:
        if isinstance(raw, bool) or isinstance(raw, (list, dict)):
            raise ValueError
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        return result(None, "invalid", "malformed_or_nonfinite")
    try:
        value = convert(
            value,
            unit,
            profile.unit,
            pressure_kind=profile.pressure_kind,
            raw_pressure_kind=pressure_kind,
        )
    except ValueError as error:
        return result(None, "invalid", str(error))
    if not math.isfinite(value) or not profile.physical[0] <= value <= profile.physical[1]:
        return result(None, "invalid", "outside_source_physical_range")
    if not profile.typical[0] <= value <= profile.typical[1]:
        return result(value, "flagged", "plausible_outlier")
    return result(value, "valid")


def essential_supported(values, profiles):
    return all(
        channel in values and values[channel].value is not None
        for channel, profile in profiles.items()
        if profile.essential
    )


def causal_fill(previous, previous_at, at, as_of, profile):
    """Bounded last-known-value fill, never moving future observations backwards."""
    if (
        previous is None
        or previous.value is None
        or at > as_of
        or previous_at > at
        or previous_at.tzinfo is None
        or at.tzinfo is None
        or as_of.tzinfo is None
    ):
        return None, False
    if 0 <= (at - previous_at).total_seconds() <= profile.max_fill_seconds:
        return previous.value, True
    return None, False
