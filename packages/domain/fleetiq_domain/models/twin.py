"""Append-only projection inputs and reproducible, immutable state snapshots."""

from fleetiq_domain.models import operations  # noqa: F401
from fleetiq_domain.models.schema import entity

TwinEvent = entity(
    "twin_event",
    {
        "aircraft_id": "uuid:required",
        "kind": "text:required",
        "occurred_at": "time:required",
        "recorded_at": "time:required",
        "revision": "int:required",
        "payload": "json:required",
        "actor_id": "uuid:required",
        "supersedes_id": "uuid:optional",
    },
    refs={"aircraft_id": "aircraft", "actor_id": "app_user", "supersedes_id": "twin_event"},
    checks=(
        "kind IN ('installed','removed','telemetry','prediction','maintenance','status')",
        "recorded_at>=occurred_at AND revision>0",
        "supersedes_id IS NULL OR supersedes_id<>id",
    ),
    unique=(("aircraft_id", "revision"),),
)
TwinSnapshot = entity(
    "twin_snapshot",
    {
        "aircraft_id": "uuid:required",
        "as_of": "time:required",
        "source_cutoff": "time:required",
        "revision": "int:required",
        "projection_version": "text:required",
        "state": "json:required",
        "state_hash": "text:required",
        "events_hash": "text:required",
    },
    refs={"aircraft_id": "aircraft"},
    checks=(
        "source_cutoff<=as_of AND revision>=0",
        "state_hash ~ '^[0-9a-f]{64}$'",
        "events_hash ~ '^[0-9a-f]{64}$'",
    ),
    unique=(("aircraft_id", "as_of", "source_cutoff", "projection_version", "events_hash"),),
)
EXTRA = [
    f"CREATE TRIGGER immutable_{t} BEFORE UPDATE OR DELETE ON {t} FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    for t in ("twin_event", "twin_snapshot")
]
POST_GRANTS = ["GRANT INSERT ON twin_event,twin_snapshot TO fleetiq_app"]
