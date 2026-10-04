"""Human-authorized work, immutable procedure revisions and audited transitions."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import AddConstraint

from fleetiq_domain.models.history import Inspection, MaintenanceEvent
from fleetiq_domain.models.schema import entity

STATES = (
    "new",
    "engineering_review",
    "accepted",
    "rejected",
    "planner_draft",
    "schedule_proposed",
    "schedule_approved",
    "executing",
    "inspection_pending",
    "released",
    "held",
    "closed",
)
STATE_CHECK = "state IN (" + ",".join(repr(s) for s in STATES) + ")"
TRANSITION_PERMISSIONS = {
    "engineering_review": "technical:approve",
    "accepted": "technical:approve",
    "rejected": "technical:approve",
    "planner_draft": "workorder:draft",
    "schedule_proposed": "workorder:draft",
    "schedule_approved": "schedule:approve",
    "executing": "task:execute",
    "inspection_pending": "task:execute",
    "released": "release:record",
    "held": "release:record",
    "closed": "release:record",
}

ProcedureRevision = entity(
    "procedure_revision",
    {
        "code": "text:required",
        "revision": "text:required",
        "authority_label": "text:required",
        "duration_slots": "int:required",
        "skills": "json:required",
        "part_requirements": "json:required",
    },
    checks=("duration_slots > 0",),
    unique=(("code", "revision"),),
)
Recommendation = entity(
    "recommendation",
    {
        "aircraft_id": "uuid:required",
        "component_id": "uuid:required",
        "policy_version": "text:required",
        "urgency": "text:required",
        "rationale": "json:required",
        "state": "text:required:'new'",
        "version": "int:required:0",
    },
    refs={"aircraft_id": "aircraft", "component_id": "component"},
    checks=("state IN ('new','engineering_review','accepted','rejected')", "version >= 0"),
)
RecommendationEvidence = entity(
    "recommendation_evidence",
    {
        "recommendation_id": "uuid:required",
        "prediction_id": "uuid:required",
    },
    refs={"recommendation_id": "recommendation"},
    unique=(("recommendation_id", "prediction_id"),),
)
WorkOrder = entity(
    "work_order",
    {
        "aircraft_id": "uuid:required",
        "component_id": "uuid:required",
        "recommendation_id": "uuid:required",
        "state": "text:required:'new'",
        "version": "int:required:0",
        "technical_scope": "json:optional",
        "plan_approval": "json:optional",
    },
    refs={
        "aircraft_id": "aircraft",
        "component_id": "component",
        "recommendation_id": "recommendation",
    },
    checks=(STATE_CHECK, "version >= 0"),
)
MaintenanceTask = entity(
    "maintenance_task",
    {
        "work_order_id": "uuid:required",
        "procedure_revision_id": "uuid:required",
        "duration_slots": "int:required",
        "deadline": "time:optional",
        "state": "text:required:'pending'",
        "version": "int:required:0",
    },
    refs={"work_order_id": "work_order", "procedure_revision_id": "procedure_revision"},
    checks=(
        "duration_slots > 0 AND version >= 0",
        "state IN ('pending','executing','completed','held')",
    ),
)
TaskDependency = entity(
    "task_dependency",
    {
        "predecessor_id": "uuid:required",
        "successor_id": "uuid:required",
    },
    refs={"predecessor_id": "maintenance_task", "successor_id": "maintenance_task"},
    checks=("predecessor_id <> successor_id",),
    unique=(("predecessor_id", "successor_id"),),
)
Approval = entity(
    "approval",
    {
        "work_order_id": "uuid:required",
        "actor_id": "uuid:required",
        "target_kind": "text:required",
        "target_id": "uuid:required",
        "action": "text:required",
        "reason": "text:required",
        "target_version": "int:required",
        "occurred_at": "time:required:now()",
    },
    refs={"work_order_id": "work_order"},
    checks=("target_version >= 0", "length(trim(reason)) > 0"),
    computed={"target_kind": "'work_order'::text", "target_id": "work_order_id"},
)

sa.Index(
    "ix_work_aircraft_state", WorkOrder.organization_id, WorkOrder.aircraft_id, WorkOrder.state
)
sa.Index(
    "ix_task_work_state",
    MaintenanceTask.organization_id,
    MaintenanceTask.work_order_id,
    MaintenanceTask.state,
)

EXTRA = []
for model, field, target in (
    (MaintenanceEvent, "task_id", "maintenance_task"),
    (Inspection, "task_id", "maintenance_task"),
    (Inspection, "procedure_revision_id", "procedure_revision"),
):
    constraint = sa.ForeignKeyConstraint(
        ["organization_id", field],
        [f"{target}.organization_id", f"{target}.id"],
        name=f"fk_{model.__table__.name}_{field}",
        ondelete="RESTRICT",
    )
    model.__table__.append_constraint(constraint)
    EXTRA.append(str(AddConstraint(constraint).compile(dialect=dialect())))
for table in ("approval", "procedure_revision"):
    EXTRA.append(
        f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
EXTRA += [
    """CREATE OR REPLACE FUNCTION fleetiq_validate_inspection() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
IF NOT EXISTS (SELECT 1 FROM maintenance_task t JOIN work_order w ON
w.organization_id=t.organization_id AND w.id=t.work_order_id
JOIN maintenance_event e ON e.organization_id=w.organization_id AND e.id=NEW.maintenance_event_id
WHERE t.organization_id=NEW.organization_id AND t.id=NEW.task_id AND t.state='completed'
AND t.procedure_revision_id=NEW.procedure_revision_id AND e.task_id=t.id
AND e.aircraft_id=w.aircraft_id AND e.component_id=w.component_id
AND NEW.completed_at>=e.occurred_at) THEN
RAISE EXCEPTION 'inspection requires completed matching task, procedure and evidence'; END IF;
RETURN NEW; END $$""",
    "CREATE TRIGGER validate_inspection BEFORE INSERT ON inspection FOR EACH ROW "
    "EXECUTE FUNCTION fleetiq_validate_inspection()",
]

EDGES = {
    "new": {"engineering_review"},
    "engineering_review": {"accepted", "rejected"},
    "accepted": {"planner_draft"},
    "planner_draft": {"schedule_proposed"},
    "schedule_proposed": {"schedule_approved", "planner_draft"},
    "schedule_approved": {"executing"},
    "executing": {"inspection_pending", "held"},
    "inspection_pending": {"released", "held"},
    "held": {"executing"},
    "released": {"closed"},
}


def transition_work(
    connection,
    organization_id,
    work_id,
    expected_version,
    target,
    *,
    actor_id,
    permissions,
    reason,
    approval_scope=None,
):
    """Caller supplies authenticated permissions; stale or unauthorized changes never persist."""
    w = WorkOrder.__table__
    row = (
        connection.execute(
            sa.select(w)
            .where(w.c.organization_id == organization_id, w.c.id == work_id)
            .with_for_update()
        )
        .mappings()
        .one()
    )
    if row["version"] != expected_version:
        raise ValueError("stale work order version")
    if target not in EDGES.get(row["state"], set()):
        raise ValueError("invalid workflow transition")
    required = TRANSITION_PERMISSIONS[target]
    if required not in permissions:
        raise PermissionError(required)
    if not reason.strip():
        raise ValueError("reason required")
    changes = dict(state=target, version=expected_version + 1)
    if target == "accepted":
        r = Recommendation.__table__
        accepted = connection.scalar(
            sa.select(r.c.state).where(
                r.c.organization_id == organization_id, r.c.id == row["recommendation_id"]
            )
        )
        if accepted != "accepted" or not approval_scope:
            raise ValueError("accepted recommendation and technical scope required")
        changes["technical_scope"] = approval_scope
    if target == "schedule_approved":
        if not row["technical_scope"] or not approval_scope:
            raise ValueError("technical scope and separate plan approval required")
        changes["plan_approval"] = approval_scope
    if target in {"executing", "released"} and (
        not row["technical_scope"] or not row["plan_approval"]
    ):
        raise ValueError("technical and plan approvals required")
    if target == "released":
        tasks = (
            connection.execute(
                sa.select(MaintenanceTask.__table__)
                .where(
                    MaintenanceTask.organization_id == organization_id,
                    MaintenanceTask.work_order_id == work_id,
                )
                .with_for_update()
            )
            .mappings()
            .all()
        )
        if not tasks or any(t["state"] != "completed" for t in tasks):
            raise ValueError("completed tasks required for release")
        for task in tasks:
            result = connection.scalar(
                sa.select(Inspection.result)
                .where(
                    Inspection.organization_id == organization_id,
                    Inspection.task_id == task["id"],
                    Inspection.procedure_revision_id == task["procedure_revision_id"],
                )
                .order_by(Inspection.completed_at.desc(), Inspection.created_at.desc())
                .limit(1)
            )
            if result != "pass":
                raise ValueError("passing inspection required for every task")
    connection.execute(
        sa.update(w)
        .where(
            w.c.id == work_id,
            w.c.organization_id == organization_id,
            w.c.version == expected_version,
        )
        .values(**changes)
    )
    connection.execute(
        sa.insert(Approval).values(
            organization_id=organization_id,
            work_order_id=work_id,
            actor_id=actor_id,
            action=target,
            reason=reason,
            target_version=expected_version + 1,
        )
    )
    return expected_version + 1


def add_dependency(connection, organization_id, predecessor_id, successor_id):
    """Serialize graph edits per tenant so concurrent checks cannot introduce a cycle."""
    connection.execute(
        sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:org,0))"),
        {"org": str(organization_id)},
    )
    tasks = connection.execute(
        sa.select(MaintenanceTask.id, MaintenanceTask.work_order_id).where(
            MaintenanceTask.organization_id == organization_id,
            MaintenanceTask.id.in_([predecessor_id, successor_id]),
        )
    ).all()
    if len(tasks) != 2 or tasks[0].work_order_id != tasks[1].work_order_id:
        raise ValueError("dependencies require distinct tasks in the same scoped work order")
    edges = connection.execute(
        sa.select(TaskDependency.predecessor_id, TaskDependency.successor_id).where(
            TaskDependency.organization_id == organization_id
        )
    ).all()
    graph = {}
    for first, second in edges:
        graph.setdefault(first, []).append(second)
    pending, visited = [successor_id], set()
    while pending:
        node = pending.pop()
        if node == predecessor_id:
            raise ValueError("dependency cycle")
        if node not in visited:
            visited.add(node)
            pending.extend(graph.get(node, []))
    connection.execute(
        sa.insert(TaskDependency).values(
            organization_id=organization_id,
            predecessor_id=predecessor_id,
            successor_id=successor_id,
        )
    )
