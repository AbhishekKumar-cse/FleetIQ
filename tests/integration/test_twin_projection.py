from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fleetiq_domain.authorization import Principal
from fleetiq_domain.models.components import Component, Installation
from fleetiq_domain.models.operations import Role, RoleAssignment
from fleetiq_domain.models.twin import TwinSnapshot
from fleetiq_domain.twin import build_snapshot, record_event

pytestmark = pytest.mark.integration


def test_rebuild_replacement_supersession_late_telemetry_and_immutable_clone(domain_connection):
    c, ids = domain_connection
    org, aircraft = ids["organization"], ids["aircraft"]
    role = c.scalar(
        sa.insert(Role)
        .values(organization_id=org, code="twin-fixture", permissions=["asset:write", "fleet:read"])
        .returning(Role.id)
    )
    c.execute(
        sa.insert(RoleAssignment).values(
            organization_id=org, user_id=ids["user"], role_id=role, scope_kind="organization"
        )
    )
    principal = Principal(org, ids["user"])
    start = datetime(2026, 1, 1, tzinfo=UTC)
    replaced = start + timedelta(days=1)
    later = replaced + timedelta(hours=1)
    c.execute(
        sa.update(Installation)
        .where(Installation.id == ids["installation"])
        .values(removed_at=replaced)
    )
    component, installation = uuid4(), uuid4()
    c.execute(
        sa.insert(Component).values(
            id=component,
            organization_id=org,
            serial="NEW-SERIAL",
            kind="engine",
            part_id=ids["part"],
        )
    )
    c.execute(
        sa.insert(Installation).values(
            id=installation,
            organization_id=org,
            aircraft_id=aircraft,
            component_id=component,
            position="engine",
            installed_at=replaced,
        )
    )

    def event(kind, at, payload, *, recorded=None, supersedes=None):
        return record_event(
            c,
            principal,
            aircraft,
            kind=kind,
            occurred_at=at,
            recorded_at=recorded or at,
            payload=payload,
            supersedes_id=supersedes,
        )

    old = str(ids["installation"])
    event("installed", start, {"installation_id": old})
    event(
        "telemetry",
        start + timedelta(hours=2),
        {"installation_id": old, "channel": "temperature", "value": 800, "quality": "valid"},
    )
    event("removed", replaced, {"installation_id": old})
    event("installed", replaced, {"installation_id": str(installation)})
    observation = event(
        "telemetry",
        replaced + timedelta(minutes=10),
        {
            "installation_id": str(installation),
            "channel": "temperature",
            "value": 700,
            "quality": "valid",
        },
    )
    early = build_snapshot(c, principal, aircraft, as_of=later, source_cutoff=later)
    event(
        "telemetry",
        replaced + timedelta(minutes=10),
        {
            "installation_id": str(installation),
            "channel": "temperature",
            "value": 710,
            "quality": "valid",
        },
        recorded=later + timedelta(hours=1),
        supersedes=observation,
    )
    event(
        "telemetry",
        later + timedelta(hours=3),
        {
            "installation_id": str(installation),
            "channel": "temperature",
            "value": 999,
            "quality": "valid",
        },
    )
    replay = build_snapshot(c, principal, aircraft, as_of=later, source_cutoff=later)
    assert replay.state_hash == early.state_hash
    assert c.scalar(sa.select(sa.func.count()).select_from(TwinSnapshot)) == 1
    current = build_snapshot(
        c,
        principal,
        aircraft,
        as_of=later + timedelta(hours=2),
        source_cutoff=later + timedelta(hours=2),
    )
    assert current.state["active_positions"]["engine"] == str(installation)
    assert (
        current.state["installations"][str(installation)]["sensors"]["temperature"]["value"] == 710
    )
    assert current.state["installations"][old]["sensors"]["temperature"]["value"] == 800
    assert current.events_hash != early.events_hash
    clone = current.simulation_clone()
    with pytest.raises(TypeError):
        clone.state["recorded_status"] = "serviceable"
    detached = clone.mutable_copy()
    detached["installations"][str(installation)]["sensors"]["temperature"]["value"] = -1
    assert (
        current.state["installations"][str(installation)]["sensors"]["temperature"]["value"] == 710
    )
    with pytest.raises(ValueError, match="installation"):
        event(
            "telemetry",
            later,
            {"installation_id": old, "channel": "temperature", "value": 900, "quality": "valid"},
        )
