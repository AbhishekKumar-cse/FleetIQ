"""Append-only occurred/recorded evidence with explicit corrections and interventions."""

from fleetiq_domain.models.schema import entity

Flight = entity(
    "flight",
    {
        "aircraft_id": "uuid:required",
        "start": "time:required",
        "end": "time:required",
        "duration_hours": "number:required",
        "flight_cycles": "int:required",
        "landing_cycles": "int:required",
        "context": "json:optional",
    },
    refs={"aircraft_id": "aircraft"},
    checks=('"end" > start', "duration_hours > 0 AND flight_cycles >= 0 AND landing_cycles >= 0"),
)
UsageEvent = entity(
    "usage_event",
    {
        "installation_id": "uuid:required",
        "observed_at": "time:required",
        "recorded_at": "time:required",
        "hours_increment": "number:required",
        "cycles_increment": "int:required",
        "reset_reason": "text:optional",
    },
    refs={"installation_id": "installation"},
    checks=(
        "recorded_at >= observed_at",
        "hours_increment >= 0 AND cycles_increment >= 0 AND (hours_increment > 0 OR cycles_increment > 0 OR reset_reason IS NOT NULL)",
    ),
)
MaintenanceEvent = entity(
    "maintenance_event",
    {
        "aircraft_id": "uuid:required",
        "component_id": "uuid:required",
        "task_id": "uuid:optional",
        "occurred_at": "time:required",
        "recorded_at": "time:required",
        "action": "text:required",
        "actor_id": "uuid:required",
        "supersedes_id": "uuid:optional",
    },
    refs={
        "aircraft_id": "aircraft",
        "component_id": "component",
        "supersedes_id": "maintenance_event",
    },
    checks=("recorded_at >= occurred_at", "supersedes_id IS NULL OR supersedes_id <> id"),
)
FailureEvent = entity(
    "failure_event",
    {
        "component_id": "uuid:required",
        "observed_at": "time:required",
        "confirmed_at": "time:optional",
        "recorded_at": "time:required",
        "mode": "text:required",
        "confirmed_by": "uuid:optional",
        "confirmation_evidence": "text:optional",
        "eligibility": "text:required",
    },
    refs={"component_id": "component"},
    checks=(
        "recorded_at >= observed_at",
        "confirmed_at IS NULL OR (confirmed_at >= observed_at AND recorded_at >= confirmed_at)",
        "eligibility IN ('unconfirmed','confirmed','censored','intervened')",
        "eligibility <> 'confirmed' OR (confirmed_at IS NOT NULL AND confirmed_by IS NOT NULL AND confirmation_evidence IS NOT NULL)",
    ),
)
Inspection = entity(
    "inspection",
    {
        "maintenance_event_id": "uuid:required",
        "task_id": "uuid:optional",
        "procedure_revision_id": "uuid:optional",
        "result": "text:required",
        "inspector_id": "uuid:required",
        "completed_at": "time:required",
    },
    refs={"maintenance_event_id": "maintenance_event"},
    checks=("result IN ('pass','fail','unknown')",),
)
EXTRA = [
    """CREATE OR REPLACE FUNCTION fleetiq_immutable_evidence() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
RAISE EXCEPTION 'append-only evidence; append a superseding event'; END $$"""
]
for name in ("flight", "usage_event", "maintenance_event", "failure_event", "inspection"):
    EXTRA.append(
        f"CREATE TRIGGER immutable_{name} BEFORE UPDATE OR DELETE ON {name} FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
EXTRA += [
    "CREATE INDEX ix_failure_component_time ON failure_event(organization_id,component_id,observed_at)",
    "CREATE INDEX ix_maintenance_aircraft_time ON maintenance_event(organization_id,aircraft_id,occurred_at)",
]
