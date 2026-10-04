"""Validated source profiles. Unknown sources/channels require a reviewed profile."""

from dataclasses import dataclass
from math import isfinite
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[4]


@dataclass(frozen=True)
class Profile:
    unit: str
    essential: bool
    physical: tuple[float, float]
    typical: tuple[float, float]
    max_fill_seconds: float
    spike_delta: float
    pressure_kind: str | None = None

    def __post_init__(self):
        lo, hi = self.physical
        normal_lo, normal_hi = self.typical
        if not all(
            isfinite(x)
            for x in (*self.physical, *self.typical, self.max_fill_seconds, self.spike_delta)
        ):
            raise ValueError("Nonfinite profile bound")
        if not lo <= normal_lo <= normal_hi <= hi or lo == hi:
            raise ValueError("Invalid profile range")
        if self.max_fill_seconds < 0 or self.spike_delta <= 0:
            raise ValueError("Invalid quality interval")
        if self.pressure_kind not in {None, "absolute", "gauge"}:
            raise ValueError("Unknown pressure basis")
        if self.unit in {"kPa", "Pa", "bar"} and self.pressure_kind is None:
            raise ValueError("Pressure basis must be declared")
        if self.pressure_kind == "absolute" and lo < 0:
            raise ValueError("Absolute pressure cannot be negative")


def load_profiles(source, path=None):
    document = yaml.safe_load((Path(path) if path else ROOT / "config/quality.yaml").read_text())
    try:
        return {channel: Profile(**spec) for channel, spec in document["profiles"][source].items()}
    except KeyError:
        raise ValueError("Source has no approved quality profile") from None
