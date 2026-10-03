from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fleetiq_domain.conventions import LifeUnit, UtcTimestamp
from pydantic import BaseModel, ConfigDict, ValidationError


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    aircraft_id: UUID
    as_of: UtcTimestamp
    unit: LifeUnit


def test_boundary_serializes_uuid_utc_and_native_unit():
    row = Observation(
        aircraft_id=uuid4(),
        as_of=datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=5, minutes=30))),
        unit="cycles",
    )
    assert row.as_of.tzinfo == UTC
    assert row.model_dump(mode="json")["unit"] == "cycles"
    assert row.model_dump(mode="json")["as_of"].endswith("Z")


@pytest.mark.parametrize(
    "changes",
    [
        {"as_of": datetime(2026, 1, 1)},
        {"unit": "days"},
        {"aircraft_id": "aircraft-1"},
        {"undocumented_field": 1},
    ],
)
def test_invalid_boundary_values_are_rejected(changes):
    values = {"aircraft_id": uuid4(), "as_of": datetime.now(UTC), "unit": "cycles"}
    with pytest.raises(ValidationError):
        Observation(**(values | changes))
