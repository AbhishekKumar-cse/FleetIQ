"""Separate recorded status, approved plans and immutable simulation snapshots."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import AddConstraint

from fleetiq_domain.models.inventory import PartReservation
from fleetiq_domain.models.schema import entity

STATUS = "status IN ('serviceable','maintenance','grounded_other','unknown')"
AircraftStatusEvent = entity(
    "aircraft_status_event",
    {
        "aircraft_id": "uuid:required",
        "component_id": "uuid:optional",
        "occurred_at": "time:required",
        "recorded_at": "time:required",
        "status": "text:required",
        "reason": "text:required",
        "actor_id": "uuid:required",
        "version": "int:required",
        "origin": "text:required:'recorded'",
    },
    refs={"aircraft_id": "aircraft", "component_id": "component"},
    checks=(STATUS, "recorded_at>=occurred_at", "version>=0", "origin='recorded'"),
    unique=(("aircraft_id", "version"),),
)
DowntimeInterval = entity(
    "downtime_interval",
    {
        "aircraft_id": "uuid:required",
        "component_id": "uuid:optional",
        "start": "time:required",
        "end": "time:optional",
        "reason": "text:required",
        "primary_reason_rank": "int:required",
        "recorded_at": "time:required",
    },
    refs={"aircraft_id": "aircraft", "component_id": "component"},
    checks=('"end" IS NULL OR "end">start', "recorded_at>=start", "primary_reason_rank>=0"),
)
FleetSnapshot = entity(
    "fleet_snapshot",
    {
        "fleet_id": "uuid:required",
        "as_of": "time:required",
        "source_cutoff": "time:required",
        "scope": "text:required",
        "scenario_id": "uuid:optional",
        "counts": "json:required",
        "total": "int:required",
        "serviceable": "int:required",
        "maintenance": "int:required",
        "grounded_other": "int:required",
        "unknown": "int:required",
        "inventory_state": "json:required",
        "configuration_hash": "text:required",
        "versions": "json:required",
    },
    refs={"fleet_id": "fleet"},
    checks=(
        "scope IN ('recorded','projected')",
        "(scope='projected')=(scenario_id IS NOT NULL)",
        "source_cutoff<=as_of",
        "total>=0 AND serviceable>=0 AND maintenance>=0 AND grounded_other>=0 AND unknown>=0",
        "total=serviceable+maintenance+grounded_other+unknown",
        "configuration_hash ~ '^[0-9a-f]{64}$'",
        "counts ?& ARRAY['serviceable','maintenance','grounded_other','unknown'] AND "
        "(counts->>'serviceable')::bigint=serviceable AND (counts->>'maintenance')::bigint=maintenance "
        "AND (counts->>'grounded_other')::bigint=grounded_other AND (counts->>'unknown')::bigint=unknown",
    ),
)
AircraftSnapshot = entity(
    "aircraft_snapshot",
    {
        "fleet_snapshot_id": "uuid:required",
        "aircraft_id": "uuid:required",
        "status": "text:required",
        "source_cutoff": "time:required",
        "evidence": "json:required",
        "at_risk": "bool:required:false",
    },
    refs={"fleet_snapshot_id": "fleet_snapshot", "aircraft_id": "aircraft"},
    checks=(STATUS,),
    unique=(("fleet_snapshot_id", "aircraft_id"),),
)
ResourceCalendar = entity(
    "resource_calendar",
    {
        "site_id": "uuid:required",
        "slot_start": "time:required",
        "slot_end": "time:required",
        "bay_capacity": "int:required",
        "skill_pools": "json:required",
        "version": "int:required",
    },
    refs={"site_id": "site"},
    checks=(
        "slot_end>slot_start",
        "bay_capacity>=0 AND version>=0",
        "jsonb_typeof(skill_pools)='object'",
    ),
    unique=(("site_id", "slot_start", "version"),),
)
SchedulePlan = entity(
    "schedule_plan",
    {
        "snapshot_id": "uuid:required",
        "policy_version_id": "uuid:required",
        "model_hash": "text:required",
        "policy_hash": "text:required",
        "solver_status": "text:required",
        "solver_gap": "float:optional",
        "state": "text:required:'draft'",
        "version": "int:required:0",
        "checker_passed": "bool:required:false",
        "reviewed_version": "int:optional",
        "approved_by": "uuid:optional",
    },
    refs={"snapshot_id": "fleet_snapshot", "policy_version_id": "policy_version"},
    checks=(
        "state IN ('draft','proposed','approved','rejected')",
        "version>=0",
        "solver_gap IS NULL OR (solver_gap>=0 AND solver_gap<=1)",
        "model_hash ~ '^[0-9a-f]{64}$' AND policy_hash ~ '^[0-9a-f]{64}$'",
        "state<>'approved' OR (checker_passed AND reviewed_version IS NOT NULL AND "
        "reviewed_version=version AND approved_by IS NOT NULL)",
    ),
)
ScheduleAssignment = entity(
    "schedule_assignment",
    {
        "plan_id": "uuid:required",
        "task_id": "uuid:required",
        "site_id": "uuid:required",
        "start": "time:required",
        "end": "time:required",
        "resource_commitments": "json:required",
    },
    refs={"plan_id": "schedule_plan", "task_id": "maintenance_task", "site_id": "site"},
    checks=('"end">start',),
    unique=(("plan_id", "task_id"),),
)
Scenario = entity(
    "scenario",
    {
        "snapshot_id": "uuid:required",
        "owner_id": "uuid:required",
        "assumptions": "json:required",
        "changes": "json:required",
        "configuration_hash": "text:required",
        "unit_mapping": "json:required",
        "horizon_slots": "int:required",
        "replicates": "int:required",
    },
    refs={"snapshot_id": "fleet_snapshot"},
    checks=(
        "horizon_slots BETWEEN 1 AND 8760",
        "replicates BETWEEN 1 AND 1000",
        "configuration_hash ~ '^[0-9a-f]{64}$'",
    ),
)
ScenarioRun = entity(
    "scenario_run",
    {
        "scenario_id": "uuid:required",
        "seed": "int:required",
        "replicate": "int:required",
        "state": "text:required:'pending'",
        "outcome_metrics": "json:optional",
        "error": "text:optional",
    },
    refs={"scenario_id": "scenario"},
    unique=(("scenario_id", "seed", "replicate"),),
    checks=(
        "seed>=0 AND replicate>=0",
        "state IN ('pending','running','completed','failed')",
        "state<>'completed' OR outcome_metrics IS NOT NULL",
        "state<>'failed' OR error IS NOT NULL",
    ),
)
sa.Index(
    "ix_status_aircraft_time",
    AircraftStatusEvent.organization_id,
    AircraftStatusEvent.aircraft_id,
    AircraftStatusEvent.occurred_at,
)
sa.Index(
    "ix_fleet_scope_time",
    FleetSnapshot.organization_id,
    FleetSnapshot.fleet_id,
    FleetSnapshot.scope,
    FleetSnapshot.as_of,
)
sa.Index(
    "ix_downtime_aircraft_time",
    DowntimeInterval.organization_id,
    DowntimeInterval.aircraft_id,
    DowntimeInterval.start,
)
EXTRA = []
DOWN_EXTRA = []
for model, field, target in (
    (FleetSnapshot, "scenario_id", "scenario"),
    (PartReservation, "plan_id", "schedule_plan"),
):
    constraint = sa.ForeignKeyConstraint(
        ["organization_id", field],
        [f"{target}.organization_id", f"{target}.id"],
        name=f"fk_{model.__table__.name}_{field}",
        ondelete="RESTRICT",
        use_alter=True,
    )
    model.__table__.append_constraint(constraint)
    EXTRA.append(str(AddConstraint(constraint).compile(dialect=dialect())))
    DOWN_EXTRA.append(f"ALTER TABLE {model.__table__.name} DROP CONSTRAINT {constraint.name}")
for table in (
    "aircraft_status_event",
    "downtime_interval",
    "fleet_snapshot",
    "aircraft_snapshot",
    "scenario",
):
    EXTRA.append(
        f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
EXTRA += [
    """CREATE FUNCTION fleetiq_validate_scenario_scope() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
IF TG_TABLE_NAME='scenario' THEN
IF NOT EXISTS(SELECT 1 FROM fleet_snapshot f WHERE f.organization_id=NEW.organization_id
AND f.id=NEW.snapshot_id AND f.scope='recorded') THEN RAISE EXCEPTION 'scenario needs recorded baseline'; END IF;
ELSIF TG_TABLE_NAME='fleet_snapshot' THEN
IF current_user='fleetiq_worker' AND NEW.scope<>'projected' THEN RAISE EXCEPTION 'simulation worker cannot record live snapshots'; END IF;
IF NEW.scenario_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM scenario s JOIN fleet_snapshot f
ON f.id=s.snapshot_id AND f.organization_id=s.organization_id WHERE s.id=NEW.scenario_id
AND s.organization_id=NEW.organization_id AND f.fleet_id=NEW.fleet_id AND f.source_cutoff=NEW.source_cutoff)
THEN RAISE EXCEPTION 'projection must retain scoped baseline cutoff'; END IF;
ELSIF TG_TABLE_NAME='aircraft_snapshot' THEN
IF NOT EXISTS(SELECT 1 FROM fleet_snapshot f JOIN aircraft a ON a.fleet_id=f.fleet_id
AND a.organization_id=f.organization_id WHERE f.id=NEW.fleet_snapshot_id AND f.organization_id=NEW.organization_id
AND a.id=NEW.aircraft_id AND f.source_cutoff=NEW.source_cutoff AND
(current_user<>'fleetiq_worker' OR f.scope='projected')) THEN RAISE EXCEPTION 'aircraft snapshot scope/cutoff mismatch'; END IF;
ELSIF TG_TABLE_NAME='scenario_run' THEN
IF NOT EXISTS(SELECT 1 FROM scenario s WHERE s.id=NEW.scenario_id AND s.organization_id=NEW.organization_id
AND NEW.replicate<s.replicates) THEN RAISE EXCEPTION 'replicate outside scenario'; END IF;
END IF; RETURN NEW; END $$""",
    """CREATE FUNCTION fleetiq_validate_skill_pools() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE value jsonb; n bigint; distinct_n bigint; BEGIN
FOR value IN SELECT v FROM jsonb_each(NEW.skill_pools) e(k,v) LOOP
IF jsonb_typeof(value)<>'array' THEN RAISE EXCEPTION 'skill pools require worker arrays'; END IF; END LOOP;
SELECT count(*),count(DISTINCT worker) INTO n,distinct_n FROM jsonb_each(NEW.skill_pools) e(k,v),
jsonb_array_elements_text(v) a(worker); IF n<>distinct_n THEN RAISE EXCEPTION 'worker counted in multiple pools'; END IF;
RETURN NEW; END $$""",
    "CREATE TRIGGER validate_skill_pools BEFORE INSERT OR UPDATE ON resource_calendar FOR EACH ROW EXECUTE FUNCTION fleetiq_validate_skill_pools()",
    "GRANT INSERT ON scenario_run,fleet_snapshot,aircraft_snapshot TO fleetiq_worker",
    "GRANT UPDATE (state,outcome_metrics,error) ON scenario_run TO fleetiq_worker",
]
for table in ("scenario", "fleet_snapshot", "aircraft_snapshot", "scenario_run"):
    EXTRA.append(
        f"CREATE TRIGGER validate_scope_{table} BEFORE INSERT OR UPDATE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION fleetiq_validate_scenario_scope()"
    )
    DOWN_EXTRA.append(f"DROP TRIGGER validate_scope_{table} ON {table}")
DOWN_EXTRA += [
    "DROP FUNCTION fleetiq_validate_scenario_scope()",
    "DROP TRIGGER validate_skill_pools ON resource_calendar",
    "DROP FUNCTION fleetiq_validate_skill_pools()",
]
