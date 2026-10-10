"""Installation-scoped state projection, replay and read-only simulation snapshots."""

import json
from dataclasses import dataclass
from datetime import timedelta
from types import MappingProxyType
from uuid import UUID

import sqlalchemy as sa
from fleetiq_evaluation.splits import content_hash
from sqlalchemy.dialects.postgresql import insert as pg_insert

from fleetiq_domain.authorization import require
from fleetiq_domain.models.components import Component, Installation
from fleetiq_domain.models.operations import AuditEvent, EventOutbox
from fleetiq_domain.models.twin import TwinEvent, TwinSnapshot

VERSION = "installation-state-twin-v1"


def freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(freeze(v) for v in value)
    return value


@dataclass(frozen=True)
class StateSnapshot:
    state_json: str
    state_hash: str
    events_hash: str
    revision: int
    projection_version: str = VERSION

    @property
    def state(self):
        return freeze(json.loads(self.state_json))

    def simulation_clone(self):
        return StateSnapshot(
            self.state_json,
            self.state_hash,
            self.events_hash,
            self.revision,
            self.projection_version,
        )

    def mutable_copy(self):
        return json.loads(self.state_json)


def record_event(
    c, principal, aircraft_id, *, kind, occurred_at, recorded_at, payload, supersedes_id=None
):
    permission = {"status": "release:record", "maintenance": "task:execute"}.get(
        kind, "asset:write"
    )
    require(c, principal, permission, aircraft_id=aircraft_id)
    if kind not in {"installed", "removed", "telemetry", "prediction", "maintenance", "status"}:
        raise ValueError("Unknown twin event kind")
    if any(d.tzinfo is None for d in (occurred_at, recorded_at)) or recorded_at < occurred_at:
        raise ValueError("Aware occurred/recorded evidence required")
    payload = json.loads(json.dumps(payload, allow_nan=False))
    if kind != "status":
        installation = (
            c.execute(
                sa.select(Installation.__table__, Component.serial)
                .join(
                    Component,
                    sa.and_(
                        Component.id == Installation.component_id,
                        Component.organization_id == Installation.organization_id,
                    ),
                )
                .where(
                    Installation.organization_id == principal.organization_id,
                    Installation.aircraft_id == aircraft_id,
                    Installation.id == UUID(payload["installation_id"]),
                )
            )
            .mappings()
            .one()
        )
        if occurred_at < installation["installed_at"] or (
            kind != "removed"
            and installation["removed_at"] is not None
            and occurred_at >= installation["removed_at"]
        ):
            raise ValueError("Event outside installation window")
        if kind == "installed":
            if occurred_at != installation["installed_at"]:
                raise ValueError("Installation event timestamp mismatch")
            payload.update(
                component_id=str(installation["component_id"]),
                serial=installation["serial"],
                position=installation["position"],
            )
        if kind == "removed" and occurred_at != installation["removed_at"]:
            raise ValueError("Removal event timestamp mismatch")
    if kind == "status" and payload.get("status") not in {
        "serviceable",
        "maintenance",
        "grounded_other",
        "unknown",
    }:
        raise ValueError("Unknown recorded status")
    if kind == "telemetry" and (
        not payload.get("channel")
        or payload.get("quality") not in {"valid", "flagged", "missing", "invalid"}
    ):
        raise ValueError("Channel and observation quality required")
    if kind == "maintenance" and not payload.get("work_id"):
        raise ValueError("Maintenance work identity required")
    org = principal.organization_id
    c.execute(
        sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
        {"key": "twin:" + str(aircraft_id)},
    )
    revision = (
        c.scalar(
            sa.select(sa.func.max(TwinEvent.revision)).where(
                TwinEvent.organization_id == org, TwinEvent.aircraft_id == aircraft_id
            )
        )
        or 0
    ) + 1
    if supersedes_id:
        old = (
            c.execute(
                sa.select(TwinEvent.__table__).where(
                    TwinEvent.id == supersedes_id,
                    TwinEvent.organization_id == org,
                    TwinEvent.aircraft_id == aircraft_id,
                )
            )
            .mappings()
            .one()
        )
        if (
            old["kind"] != kind
            or old["payload"].get("installation_id") != payload.get("installation_id")
            or recorded_at < old["recorded_at"]
        ):
            raise ValueError("Supersession cannot cross kind/installation or recorded time")
    event_id = c.scalar(
        sa.insert(TwinEvent)
        .values(
            organization_id=org,
            aircraft_id=aircraft_id,
            kind=kind,
            occurred_at=occurred_at,
            recorded_at=recorded_at,
            revision=revision,
            payload=payload,
            actor_id=principal.user_id,
            supersedes_id=supersedes_id,
        )
        .returning(TwinEvent.id)
    )
    c.execute(
        sa.insert(EventOutbox).values(
            organization_id=org,
            scope_kind="aircraft",
            scope_id=aircraft_id,
            kind="twin.event",
            payload=dict(event_id=str(event_id), revision=revision),
        )
    )
    c.execute(
        sa.insert(AuditEvent).values(
            organization_id=org,
            actor_id=principal.user_id,
            action="twin.event",
            target_kind="twin_event",
            target_id=event_id,
            scope_kind="aircraft",
            scope_id=aircraft_id,
            versions={"revision": revision},
            reason="Recorded installation-scoped projection evidence",
        )
    )
    return event_id


def project(events, *, aircraft_id, as_of, source_cutoff, freshness=timedelta(hours=6)):
    if any(d.tzinfo is None for d in (as_of, source_cutoff)) or source_cutoff > as_of:
        raise ValueError("Aware source cutoff at or before snapshot required")
    visible = sorted(
        (
            e
            for e in events
            if e["aircraft_id"] == aircraft_id
            and e["occurred_at"] <= as_of
            and e["recorded_at"] <= source_cutoff
        ),
        key=lambda e: (e["occurred_at"], e["revision"]),
    )
    superseded = {e["supersedes_id"] for e in visible if e.get("supersedes_id")}
    body = [
        dict(
            id=str(e["id"]),
            revision=e["revision"],
            kind=e["kind"],
            occurred_at=e["occurred_at"].isoformat(),
            recorded_at=e["recorded_at"].isoformat(),
            supersedes_id=str(e["supersedes_id"]) if e.get("supersedes_id") else None,
            payload=e["payload"],
        )
        for e in visible
    ]
    state = dict(
        aircraft_id=str(aircraft_id),
        as_of=as_of.isoformat(),
        source_cutoff=source_cutoff.isoformat(),
        recorded_status="unknown",
        installations={},
        active_positions={},
    )
    for e in visible:
        if e["id"] in superseded:
            continue
        p, kind = e["payload"], e["kind"]
        if kind == "status":
            state["recorded_status"] = p["status"]
            continue
        key = p["installation_id"]
        if kind == "installed":
            if p["position"] in state["active_positions"]:
                raise ValueError("Overlapping twin installation events")
            state["installations"][key] = p | dict(
                installed_at=e["occurred_at"].isoformat(),
                removed_at=None,
                sensors={},
                predictions={},
                maintenance={},
            )
            state["active_positions"][p["position"]] = key
        elif kind == "removed":
            if key not in state["installations"]:
                raise ValueError("Removal without installation event")
            row = state["installations"][key]
            row["removed_at"] = e["occurred_at"].isoformat()
            state["active_positions"].pop(row["position"], None)
        else:
            if key not in state["installations"] or state["installations"][key]["removed_at"]:
                raise ValueError("Observation outside projected installation")
            row = state["installations"][key]
            if kind == "telemetry":
                row["sensors"][p["channel"]] = p | dict(
                    observed_at=e["occurred_at"].isoformat(),
                    fresh=as_of - e["occurred_at"] <= freshness and p["quality"] == "valid",
                )
            elif kind == "prediction":
                row["predictions"][p["task"]] = p | dict(as_of=e["occurred_at"].isoformat())
            else:
                row["maintenance"][p["work_id"]] = p | dict(as_of=e["occurred_at"].isoformat())
    return StateSnapshot(
        json.dumps(state, sort_keys=True, allow_nan=False),
        content_hash(state),
        content_hash(body),
        max((e["revision"] for e in visible), default=0),
    )


def build_snapshot(c, principal, aircraft_id, *, as_of, source_cutoff, persist=True):
    require(c, principal, "fleet:read", aircraft_id=aircraft_id)
    events = (
        c.execute(
            sa.select(TwinEvent.__table__).where(
                TwinEvent.organization_id == principal.organization_id,
                TwinEvent.aircraft_id == aircraft_id,
                TwinEvent.occurred_at <= as_of,
                TwinEvent.recorded_at <= source_cutoff,
            )
        )
        .mappings()
        .all()
    )
    snapshot = project(events, aircraft_id=aircraft_id, as_of=as_of, source_cutoff=source_cutoff)
    if persist:
        c.execute(
            pg_insert(TwinSnapshot)
            .values(
                organization_id=principal.organization_id,
                aircraft_id=aircraft_id,
                as_of=as_of,
                source_cutoff=source_cutoff,
                state=snapshot.mutable_copy(),
                state_hash=snapshot.state_hash,
                events_hash=snapshot.events_hash,
                revision=snapshot.revision,
                projection_version=VERSION,
            )
            .on_conflict_do_nothing()
        )
    return snapshot
