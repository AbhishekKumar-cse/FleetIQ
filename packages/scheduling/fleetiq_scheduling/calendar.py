"""Explicit seven-day calendar; durations round upward to two-hour slots."""

import math
from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class Calendar:
    starts_at: object
    slot_hours: int = 2
    days: int = 7

    def __post_init__(self):
        if self.starts_at.tzinfo is None or self.slot_hours != 2 or self.days != 7:
            raise ValueError("Aware seven-day/two-hour demo calendar required")

    @property
    def slots(self):
        return tuple(self.starts_at + timedelta(hours=self.slot_hours * k) for k in range(84))

    def duration(self, hours):
        if not math.isfinite(hours) or hours <= 0:
            raise ValueError("Positive finite procedure duration required")
        return math.ceil(hours / self.slot_hours)

    def starts(self, hours, *, earliest=None, deadline=None, candidates=None):
        duration = self.duration(hours)
        earliest = earliest or self.starts_at
        deadline = deadline or self.starts_at + timedelta(days=self.days)
        if earliest.tzinfo is None or deadline.tzinfo is None or deadline <= earliest:
            raise ValueError("Aware positive task window required")
        candidates = tuple(range(84)) if candidates is None else tuple(candidates)
        if len(set(candidates)) != len(candidates) or any(
            type(k) is not int or not 0 <= k < 84 for k in candidates
        ):
            raise ValueError("Unique in-horizon candidate starts required")
        return tuple(
            k
            for k in sorted(candidates)
            if k + duration <= 84
            and self.slots[k] >= earliest
            and self.slots[k] + timedelta(hours=duration * self.slot_hours) <= deadline
        )
