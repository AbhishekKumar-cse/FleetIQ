"""Shared boundary types: explicit UTC timestamps and native life units."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator


class LifeUnit(StrEnum):
    CYCLES = "cycles"
    OPERATING_HOURS = "operating_hours"


def require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(UTC)


UtcTimestamp = Annotated[datetime, AfterValidator(require_utc)]
