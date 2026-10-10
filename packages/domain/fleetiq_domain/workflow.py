"""Fresh RBAC, optimistic versions and atomic human maintenance transitions."""

from contextlib import contextmanager
from uuid import UUID

import sqlalchemy as sa
from fleetiq_evaluation.splits import content_hash

from fleetiq_domain.authorization import Forbidden, require
from fleetiq_domain.models.components import Installation
from fleetiq_domain.models.history import Inspection, MaintenanceEvent
from fleetiq_domain.models.inventory import SparePart
from fleetiq_domain.models.operations import AuditEvent, EventOutbox
from fleetiq_domain.models.predictions import Prediction
from fleetiq_domain.models.work import (
    TRANSITION_PERMISSIONS,
    Approval,
    MaintenanceTask,
    ProcedureRevision,
    Recommendation,
    RecommendationEvidence,
    TaskDependency,
    WorkOrder,
    transition_work,
)


@contextmanager
def mutation(c):
    with c.begin_nested():
        previous = (
            c.scalar(sa.text("SELECT current_setting('fleetiq.workflow_mutation',true)")) or ""
        )
        c.execute(sa.text("SELECT set_config('fleetiq.workflow_mutation','allowed',true)"))
        yield
        c.execute(
            sa.text("SELECT set_config('fleetiq.workflow_mutation',:previous,true)"),
            {"previous": previous},
        )


def scoped(c, principal, model, identity, permission):
    row = (
        c.execute(
            sa.select(model.__table__).where(
                model.organization_id == principal.organization_id, model.id == identity
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Forbidden("Access denied")
    require(c, principal, permission, aircraft_id=row["aircraft_id"])
    return dict(
        c.execute(
            sa.select(model.__table__)
            .where(model.organization_id == principal.organization_id, model.id == identity)
            .with_for_update()
        )
        .mappings()
        .one()
    )


def version(row, expected):
    if type(expected) is not int or row["version"] != expected:
        raise ValueError("Stale workflow version")


def emit(c, principal, row, kind, action, before, after, reason, *, evidence=None):
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("Reason required")
    versions = dict(before=before, after=after, evidence=evidence or [])
    c.execute(
        sa.insert(AuditEvent).values(
            organization_id=principal.organization_id,
            actor_id=principal.user_id,
            action="workflow." + action,
            target_kind=kind,
            target_id=row["id"],
            scope_kind="aircraft",
            scope_id=row["aircraft_id"],
            versions=versions,
            reason=reason,
        )
    )
    c.execute(
        sa.insert(EventOutbox).values(
            organization_id=principal.organization_id,
            scope_kind="aircraft",
            scope_id=row["aircraft_id"],
            kind="workflow." + action,
            payload=dict(
                target_kind=kind,
                target_id=str(row["id"]),
                version=after,
                actor_id=str(principal.user_id),
            ),
        )
    )


def evidence_for(c, org, recommendation):
    rows = (
        c.execute(
            sa.select(Prediction.__table__)
            .join(
                RecommendationEvidence,
                sa.and_(
                    RecommendationEvidence.organization_id == Prediction.organization_id,
                    RecommendationEvidence.prediction_id == Prediction.id,
                ),
            )
            .where(
                RecommendationEvidence.organization_id == org,
                RecommendationEvidence.recommendation_id == recommendation["id"],
            )
        )
        .mappings()
        .all()
    )
    if not rows or any(
        p["component_id"] != recommendation["component_id"] or p["track"] != "synthetic_engine_demo"
        for p in rows
    ):
        raise ValueError(
            "Linked component-specific fictional demo evidence required; NASA cannot approve fleet work"
        )
    return [str(p["id"]) for p in rows]


def review_recommendation(c, principal, identity, expected_version, target, *, reason):
    with mutation(c):
        row = scoped(c, principal, Recommendation, identity, "technical:approve")
        version(row, expected_version)
        if (row["state"], target) not in {
            ("new", "engineering_review"),
            ("engineering_review", "accepted"),
            ("engineering_review", "rejected"),
        }:
            raise ValueError("Invalid recommendation transition")
        evidence = evidence_for(c, principal.organization_id, row)
        c.execute(
            sa.update(Recommendation)
            .where(
                Recommendation.id == identity,
                Recommendation.organization_id == principal.organization_id,
                Recommendation.version == expected_version,
            )
            .values(state=target, version=expected_version + 1)
        )
        emit(
            c,
            principal,
            row,
            "recommendation",
            target,
            expected_version,
            expected_version + 1,
            reason,
            evidence=evidence,
        )
        return expected_version + 1


def draft_work_order(c, principal, recommendation_id, expected_version, *, reason):
    with mutation(c):
        row = scoped(c, principal, Recommendation, recommendation_id, "workorder:draft")
        version(row, expected_version)
        if row["state"] != "accepted":
            raise ValueError("Accepted engineering recommendation required")
        evidence = evidence_for(c, principal.organization_id, row)
        existing = c.scalar(
            sa.select(WorkOrder.id).where(
                WorkOrder.organization_id == principal.organization_id,
                WorkOrder.recommendation_id == recommendation_id,
            )
        )
        if existing:
            return existing
        identity = c.scalar(
            sa.insert(WorkOrder)
            .values(
                organization_id=principal.organization_id,
                aircraft_id=row["aircraft_id"],
                component_id=row["component_id"],
                recommendation_id=recommendation_id,
                state="planner_draft",
            )
            .returning(WorkOrder.id)
        )
        emit(
            c,
            principal,
            row | dict(id=identity),
            "work_order",
            "drafted",
            None,
            0,
            reason,
            evidence=evidence,
        )
        return identity


def approved_bindings(c, principal, work):
    scope = work["technical_scope"]
    if not scope or scope.get("scope_hash") != content_hash(
        {k: v for k, v in scope.items() if k != "scope_hash"}
    ):
        raise ValueError("Approved technical scope integrity required")
    actual = (
        c.execute(
            sa.select(MaintenanceTask.__table__).where(
                MaintenanceTask.organization_id == principal.organization_id,
                MaintenanceTask.work_order_id == work["id"],
            )
        )
        .mappings()
        .all()
    )
    if {str(t["id"]) for t in actual} != {t["task_id"] for t in scope["tasks"]}:
        raise ValueError("Tasks outside approved procedure scope")
    by_id = {str(t["id"]): t for t in actual}
    for binding in scope["tasks"]:
        task = by_id[binding["task_id"]]
        procedure = (
            c.execute(
                sa.select(ProcedureRevision.__table__).where(
                    ProcedureRevision.organization_id == principal.organization_id,
                    ProcedureRevision.id == task["procedure_revision_id"],
                )
            )
            .mappings()
            .one()
        )
        if (
            str(task["procedure_revision_id"]),
            task["duration_slots"],
            procedure["part_requirements"],
            procedure["skills"],
        ) != (
            binding["procedure_revision_id"],
            binding["duration_slots"],
            binding["part_requirements"],
            binding["skills"],
        ):
            raise ValueError("Task resources differ from approved procedure revision")
    return actual


def approve_scope(c, principal, work_id, expected_version, procedure_ids, *, reason):
    with mutation(c):
        work = scoped(c, principal, WorkOrder, work_id, "technical:approve")
        version(work, expected_version)
        if (
            work["state"] != "planner_draft"
            or work["technical_scope"]
            or not procedure_ids
            or len(set(procedure_ids)) != len(procedure_ids)
        ):
            raise ValueError("Unapproved planner draft and unique known procedures required")
        recommendation = (
            c.execute(
                sa.select(Recommendation.__table__).where(
                    Recommendation.organization_id == principal.organization_id,
                    Recommendation.id == work["recommendation_id"],
                )
            )
            .mappings()
            .one()
        )
        evidence = evidence_for(c, principal.organization_id, recommendation)
        tasks = []
        for identity in procedure_ids:
            procedure = (
                c.execute(
                    sa.select(ProcedureRevision.__table__).where(
                        ProcedureRevision.organization_id == principal.organization_id,
                        ProcedureRevision.id == identity,
                    )
                )
                .mappings()
                .one()
            )
            if procedure["authority_label"] != "fictional_demo_engineering_review":
                raise ValueError("Known fictional demo procedure revision required")
            parts = procedure["part_requirements"]
            if not isinstance(parts, list) or any(
                set(p) != {"part_id", "quantity"}
                or type(p["quantity"]) is not int
                or p["quantity"] <= 0
                for p in parts
            ):
                raise ValueError("Declared procedure part quantities required")
            if len({p["part_id"] for p in parts}) != len(parts) or any(
                not c.scalar(
                    sa.select(SparePart.id).where(
                        SparePart.organization_id == principal.organization_id,
                        SparePart.id == UUID(p["part_id"]),
                    )
                )
                for p in parts
            ):
                raise ValueError("Unique scoped procedure parts required")
            task_id = c.scalar(
                sa.insert(MaintenanceTask)
                .values(
                    organization_id=principal.organization_id,
                    work_order_id=work_id,
                    procedure_revision_id=identity,
                    duration_slots=procedure["duration_slots"],
                )
                .returning(MaintenanceTask.id)
            )
            tasks.append(
                dict(
                    task_id=str(task_id),
                    procedure_revision_id=str(identity),
                    code=procedure["code"],
                    revision=procedure["revision"],
                    duration_slots=procedure["duration_slots"],
                    skills=procedure["skills"],
                    part_requirements=parts,
                )
            )
        scope = dict(
            tasks=tasks,
            policy_version=recommendation["policy_version"],
            evidence_ids=evidence,
            approved_by=str(principal.user_id),
        )
        scope["scope_hash"] = content_hash(scope)
        c.execute(
            sa.update(WorkOrder)
            .where(WorkOrder.id == work_id, WorkOrder.organization_id == principal.organization_id)
            .values(technical_scope=scope, version=expected_version + 1)
        )
        c.execute(
            sa.insert(Approval).values(
                organization_id=principal.organization_id,
                work_order_id=work_id,
                actor_id=principal.user_id,
                action="technical_scope_approved",
                reason=reason,
                target_version=expected_version + 1,
            )
        )
        emit(
            c,
            principal,
            work,
            "work_order",
            "technical_scope_approved",
            expected_version,
            expected_version + 1,
            reason,
            evidence=evidence,
        )
        return expected_version + 1


def release_guard(c, principal, work, tasks):
    if not tasks or any(t["state"] != "completed" for t in tasks):
        raise ValueError("Completed tasks required")
    for task in tasks:
        completion = (
            c.execute(
                sa.select(MaintenanceEvent.__table__)
                .where(
                    MaintenanceEvent.organization_id == principal.organization_id,
                    MaintenanceEvent.task_id == task["id"],
                    MaintenanceEvent.action == "task.completed",
                )
                .order_by(
                    MaintenanceEvent.occurred_at.desc(),
                    MaintenanceEvent.recorded_at.desc(),
                    MaintenanceEvent.id.desc(),
                )
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        if completion is None:
            raise ValueError("Task completion evidence required")
        inspection = (
            c.execute(
                sa.select(Inspection.__table__)
                .where(
                    Inspection.organization_id == principal.organization_id,
                    Inspection.task_id == task["id"],
                    Inspection.maintenance_event_id == completion["id"],
                    Inspection.procedure_revision_id == task["procedure_revision_id"],
                )
                .order_by(
                    Inspection.completed_at.desc(),
                    Inspection.created_at.desc(),
                    Inspection.id.desc(),
                )
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        if (
            not inspection
            or inspection["result"] != "pass"
            or inspection["inspector_id"] == completion["actor_id"]
        ):
            raise ValueError("Independent passing inspection of latest completion required")


def transition(c, principal, work_id, expected_version, target, *, reason, plan_approval=None):
    required = TRANSITION_PERMISSIONS.get(target)
    if required is None:
        raise ValueError("Unknown work transition")
    with mutation(c):
        work = scoped(c, principal, WorkOrder, work_id, required)
        version(work, expected_version)
        tasks = approved_bindings(c, principal, work)
        approval = None
        if target == "schedule_approved":
            if (
                not plan_approval
                or set(plan_approval) != {"scope_hash", "plan_reference", "plan_version"}
                or plan_approval["scope_hash"] != work["technical_scope"]["scope_hash"]
                or not plan_approval["plan_reference"]
                or type(plan_approval["plan_version"]) is not int
                or plan_approval["plan_version"] < 1
            ):
                raise ValueError("Separate versioned human plan approval bound to scope required")
            approval = plan_approval | dict(approved_by=str(principal.user_id))
        if target == "inspection_pending" and any(t["state"] != "completed" for t in tasks):
            raise ValueError("Technician must complete every task before inspection")
        if target == "released":
            release_guard(c, principal, work, tasks)
        if target in {"executing", "released"}:
            now = c.scalar(sa.select(sa.func.clock_timestamp()))
            installed = c.scalar(
                sa.select(Installation.id).where(
                    Installation.organization_id == principal.organization_id,
                    Installation.aircraft_id == work["aircraft_id"],
                    Installation.component_id == work["component_id"],
                    Installation.installed_at <= now,
                    sa.or_(Installation.removed_at.is_(None), Installation.removed_at > now),
                )
            )
            if not installed:
                raise ValueError(
                    "Approved component is no longer installed; review replacement separately"
                )
        updated = transition_work(
            c,
            principal.organization_id,
            work_id,
            expected_version,
            target,
            actor_id=principal.user_id,
            permissions={required},
            reason=reason,
            approval_scope=approval,
        )
        emit(
            c,
            principal,
            work,
            "work_order",
            target,
            expected_version,
            updated,
            reason,
            evidence=work["technical_scope"]["evidence_ids"],
        )
        return updated


def task_transition(c, principal, task_id, expected_version, target, *, reason, occurred_at=None):
    with mutation(c):
        task = (
            c.execute(
                sa.select(MaintenanceTask.__table__)
                .where(
                    MaintenanceTask.organization_id == principal.organization_id,
                    MaintenanceTask.id == task_id,
                )
                .with_for_update()
            )
            .mappings()
            .one()
        )
        work = scoped(c, principal, WorkOrder, task["work_order_id"], "task:execute")
        version(task, expected_version)
        approved_bindings(c, principal, work)
        if work["state"] != "executing" or (task["state"], target) not in {
            ("pending", "executing"),
            ("executing", "completed"),
            ("executing", "held"),
            ("held", "executing"),
        }:
            raise ValueError("Invalid task execution transition")
        if target == "executing":
            unmet = c.scalar(
                sa.select(sa.func.count())
                .select_from(TaskDependency)
                .join(MaintenanceTask, MaintenanceTask.id == TaskDependency.predecessor_id)
                .where(
                    TaskDependency.organization_id == principal.organization_id,
                    TaskDependency.successor_id == task_id,
                    MaintenanceTask.state != "completed",
                )
            )
            if unmet:
                raise ValueError("Task dependencies must complete first")
        now = c.scalar(sa.select(sa.func.clock_timestamp()))
        occurred_at = occurred_at or now
        if occurred_at.tzinfo is None or occurred_at > now:
            raise ValueError("Observed completion time required")
        c.execute(
            sa.update(MaintenanceTask)
            .where(
                MaintenanceTask.id == task_id,
                MaintenanceTask.organization_id == principal.organization_id,
            )
            .values(state=target, version=expected_version + 1)
        )
        event = c.scalar(
            sa.insert(MaintenanceEvent)
            .values(
                organization_id=principal.organization_id,
                aircraft_id=work["aircraft_id"],
                component_id=work["component_id"],
                task_id=task_id,
                occurred_at=occurred_at,
                recorded_at=now,
                action="task." + target,
                actor_id=principal.user_id,
            )
            .returning(MaintenanceEvent.id)
        )
        emit(
            c,
            principal,
            work | dict(id=task_id),
            "maintenance_task",
            "task." + target,
            expected_version,
            expected_version + 1,
            reason,
            evidence=[str(event)],
        )
        return expected_version + 1


def record_inspection(c, principal, task_id, expected_task_version, result, *, reason):
    with mutation(c):
        task = (
            c.execute(
                sa.select(MaintenanceTask.__table__)
                .where(
                    MaintenanceTask.organization_id == principal.organization_id,
                    MaintenanceTask.id == task_id,
                )
                .with_for_update()
            )
            .mappings()
            .one()
        )
        work = scoped(c, principal, WorkOrder, task["work_order_id"], "release:record")
        version(task, expected_task_version)
        approved_bindings(c, principal, work)
        if (
            task["state"] != "completed"
            or work["state"] not in {"inspection_pending", "held"}
            or result not in {"pass", "fail", "unknown"}
        ):
            raise ValueError("Completed scope and explicit inspection result required")
        completion = (
            c.execute(
                sa.select(MaintenanceEvent.__table__)
                .where(
                    MaintenanceEvent.organization_id == principal.organization_id,
                    MaintenanceEvent.task_id == task_id,
                    MaintenanceEvent.action == "task.completed",
                )
                .order_by(
                    MaintenanceEvent.occurred_at.desc(),
                    MaintenanceEvent.recorded_at.desc(),
                    MaintenanceEvent.id.desc(),
                )
                .limit(1)
            )
            .mappings()
            .one()
        )
        if completion["actor_id"] == principal.user_id:
            raise ValueError("Independent inspector required")
        identity = c.scalar(
            sa.insert(Inspection)
            .values(
                organization_id=principal.organization_id,
                maintenance_event_id=completion["id"],
                task_id=task_id,
                procedure_revision_id=task["procedure_revision_id"],
                result=result,
                inspector_id=principal.user_id,
                completed_at=c.scalar(sa.select(sa.func.clock_timestamp())),
            )
            .returning(Inspection.id)
        )
        emit(
            c,
            principal,
            work | dict(id=identity),
            "inspection",
            "inspection." + result,
            None,
            expected_task_version,
            reason,
            evidence=[str(completion["id"])],
        )
        return identity
