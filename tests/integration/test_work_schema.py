from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fleetiq_domain.models.history import Inspection, MaintenanceEvent
from fleetiq_domain.models.work import (
    Approval,
    MaintenanceTask,
    ProcedureRevision,
    Recommendation,
    WorkOrder,
    add_dependency,
    transition_work,
)
from sqlalchemy import insert, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError

pytestmark = pytest.mark.integration


@pytest.fixture
def work_fixture(domain_connection):
    connection, ids = domain_connection
    org = ids["organization"]
    procedure = connection.scalar(
        insert(ProcedureRevision)
        .values(
            organization_id=org,
            code="DEMO-INSPECT",
            revision="1",
            authority_label="Demo only",
            duration_slots=2,
            skills=["engineer"],
            part_requirements=[],
        )
        .returning(ProcedureRevision.id)
    )
    recommendation = connection.scalar(
        insert(Recommendation)
        .values(
            organization_id=org,
            aircraft_id=ids["aircraft"],
            component_id=ids["component"],
            policy_version="demo-v1",
            urgency="routine",
            rationale={"reason": "inspection"},
            state="accepted",
        )
        .returning(Recommendation.id)
    )
    work = connection.scalar(
        insert(WorkOrder)
        .values(
            organization_id=org,
            aircraft_id=ids["aircraft"],
            component_id=ids["component"],
            recommendation_id=recommendation,
        )
        .returning(WorkOrder.id)
    )
    tasks = [
        connection.scalar(
            insert(MaintenanceTask)
            .values(
                organization_id=org,
                work_order_id=work,
                procedure_revision_id=procedure,
                duration_slots=2,
            )
            .returning(MaintenanceTask.id)
        )
        for _ in range(3)
    ]
    return connection, ids | {"procedure": procedure, "work": work, "tasks": tasks}


def test_dependency_cycle_and_missing_procedure_rejected(work_fixture):
    connection, ids = work_fixture
    org, tasks = ids["organization"], ids["tasks"]
    add_dependency(connection, org, tasks[0], tasks[1])
    add_dependency(connection, org, tasks[1], tasks[2])
    with pytest.raises(ValueError, match="cycle"):
        add_dependency(connection, org, tasks[2], tasks[0])
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            insert(MaintenanceTask).values(
                organization_id=org,
                work_order_id=ids["work"],
                procedure_revision_id=uuid4(),
                duration_slots=2,
            )
        )
    with pytest.raises(DBAPIError), connection.begin_nested():
        connection.execute(update(ProcedureRevision).values(revision="2"))


def test_authorizations_versions_and_release_prerequisites(work_fixture):
    connection, ids = work_fixture
    actor = uuid4()

    def change(version, target, permissions, scope=None):
        return transition_work(
            connection,
            ids["organization"],
            ids["work"],
            version,
            target,
            actor_id=actor,
            permissions=permissions,
            reason="Demo review",
            approval_scope=scope,
        )

    with pytest.raises(PermissionError):
        change(0, "engineering_review", {"admin"})
    change(0, "engineering_review", {"technical:approve"})
    with pytest.raises(ValueError, match="stale"):
        change(0, "accepted", {"technical:approve"}, {"procedure": str(ids["procedure"])})
    change(1, "accepted", {"technical:approve"}, {"procedure": str(ids["procedure"])})
    change(2, "planner_draft", {"schedule:approve"})
    change(3, "schedule_proposed", {"schedule:approve"})
    with pytest.raises(ValueError, match="separate plan"):
        change(4, "schedule_approved", {"schedule:approve"})
    change(4, "schedule_approved", {"schedule:approve"}, {"plan": "demo-plan-v1"})
    change(5, "executing", {"task:execute"})
    change(6, "inspection_pending", {"task:execute"})
    with pytest.raises(ValueError, match="completed tasks"):
        change(7, "released", {"release:record"})
    when = datetime(2026, 1, 2, tzinfo=UTC)
    for task in ids["tasks"]:
        event = connection.scalar(
            insert(MaintenanceEvent)
            .values(
                organization_id=ids["organization"],
                aircraft_id=ids["aircraft"],
                component_id=ids["component"],
                task_id=task,
                occurred_at=when,
                recorded_at=when,
                action="inspection",
                actor_id=actor,
            )
            .returning(MaintenanceEvent.id)
        )
        row = dict(
            organization_id=ids["organization"],
            maintenance_event_id=event,
            task_id=task,
            procedure_revision_id=ids["procedure"],
            result="pass",
            inspector_id=actor,
            completed_at=when,
        )
        with pytest.raises(DBAPIError), connection.begin_nested():
            connection.execute(insert(Inspection).values(**row))
        connection.execute(
            update(MaintenanceTask)
            .where(MaintenanceTask.id == task)
            .values(state="completed", version=1)
        )
        if task == ids["tasks"][-1]:
            with pytest.raises(ValueError, match="passing inspection"):
                change(7, "released", {"release:record"})
        connection.execute(insert(Inspection).values(**row))
    change(7, "released", {"release:record"})
    changes = connection.execute(
        select(Approval.action, Approval.target_version)
        .where(Approval.work_order_id == ids["work"])
        .order_by(Approval.target_version)
    ).all()
    assert len(changes) == 8 and changes[-1] == ("released", 8)
    with pytest.raises(DBAPIError), connection.begin_nested():
        connection.execute(update(Approval).values(reason="rewrite"))
