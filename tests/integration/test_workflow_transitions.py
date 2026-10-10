"""Runtime role journey: stale/rejected/unauthorized writes never escape the service."""

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from fleetiq_api.settings import Settings
from fleetiq_domain.authorization import Forbidden, Principal
from fleetiq_domain.models.operations import AuditEvent, EventOutbox, Role, RoleAssignment, User
from fleetiq_domain.models.predictions import FeatureSnapshot, ModelDeployment, Prediction
from fleetiq_domain.models.work import (
    MaintenanceTask,
    ProcedureRevision,
    Recommendation,
    RecommendationEvidence,
    WorkOrder,
)
from fleetiq_domain.workflow import (
    approve_scope,
    draft_work_order,
    record_inspection,
    review_recommendation,
    task_transition,
    transition,
)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration


@pytest.fixture
def workflow_fixture(domain_connection, isolated_database):
    c, ids = domain_connection
    org, users = ids["organization"], {}
    for name, permissions in {
        "engineer": ["technical:approve"],
        "planner": ["workorder:draft", "schedule:approve"],
        "technician": ["task:execute"],
        "inspector": ["release:record"],
        "admin": ["users:manage"],
    }.items():
        user = c.scalar(
            sa.insert(User)
            .values(
                organization_id=org,
                subject=name,
                display_name=name,
                password_hash="$argon2id$fixture",
            )
            .returning(User.id)
        )
        role = c.scalar(
            sa.insert(Role)
            .values(organization_id=org, code=name, permissions=permissions)
            .returning(Role.id)
        )
        c.execute(
            sa.insert(RoleAssignment).values(
                organization_id=org, user_id=user, role_id=role, scope_kind="organization"
            )
        )
        users[name] = Principal(org, user)
    at = datetime(2026, 1, 2, tzinfo=UTC)
    feature = c.scalar(
        sa.insert(FeatureSnapshot)
        .values(
            organization_id=org,
            component_id=ids["component"],
            installation_id=ids["installation"],
            as_of=at,
            window_start=at - timedelta(hours=1),
            window_end=at,
            feature_version="demo-f1",
            input_hash="a" * 64,
            track="synthetic_engine_demo",
            vector={"vibration": 1.0},
            quality={},
        )
        .returning(FeatureSnapshot.id)
    )
    signature = dict(
        task="failure_risk",
        track="synthetic_engine_demo",
        horizon=24,
        unit="operating_hours",
        feature_version="demo-f1",
        model_version="demo-m1",
        calibration_version="disabled",
        bundle_hash="b" * 64,
    )
    deployment = c.scalar(
        sa.insert(ModelDeployment)
        .values(
            organization_id=org,
            **signature,
            applicability={"scope": "fictional_workflow_fixture"},
            effective_at=at - timedelta(days=1),
        )
        .returning(ModelDeployment.id)
    )
    prediction = c.scalar(
        sa.insert(Prediction)
        .values(
            organization_id=org,
            component_id=ids["component"],
            feature_snapshot_id=feature,
            deployment_id=deployment,
            as_of=at,
            input_hash="a" * 64,
            **signature,
            output=None,
            quality={"supported": False},
            coverage="unsupported",
            explanation_status="unsupported",
        )
        .returning(Prediction.id)
    )

    def recommendation(key):
        identity = c.scalar(
            sa.insert(Recommendation)
            .values(
                organization_id=org,
                aircraft_id=ids["aircraft"],
                component_id=ids["component"],
                policy_version="maintenance-review-v1",
                urgency="review",
                rationale={
                    "reason": "Investigate unsupported fictional sensor evidence",
                    "fixture": key,
                },
                state="engineering_review",
            )
            .returning(Recommendation.id)
        )
        c.execute(
            sa.insert(RecommendationEvidence).values(
                organization_id=org, recommendation_id=identity, prediction_id=prediction
            )
        )
        return identity

    accepted, rejected = recommendation("accepted"), recommendation("rejected")
    procedure = c.scalar(
        sa.insert(ProcedureRevision)
        .values(
            organization_id=org,
            code="investigate_sensor_departure",
            revision="demo-1",
            authority_label="fictional_demo_engineering_review",
            duration_slots=2,
            skills=["engine_diagnostics"],
            part_requirements=[{"part_id": str(ids["part"]), "quantity": 1}],
        )
        .returning(ProcedureRevision.id)
    )
    c.commit()
    application = sa.create_engine(
        make_url(Settings().database_url.get_secret_value()).set(database=isolated_database[1]),
        hide_parameters=True,
    )
    migration = sa.create_engine(isolated_database[0], hide_parameters=True)
    try:
        yield (
            application,
            migration,
            ids | dict(procedure=procedure, recommendation=accepted, rejected=rejected),
            users,
        )
    finally:
        application.dispose()
        migration.dispose()


def test_role_specific_audited_journey_inspection_and_forbidden_mutations(workflow_fixture):
    application, migration, ids, users = workflow_fixture
    rec = ids["recommendation"]
    with application.begin() as c:
        with pytest.raises(Forbidden):
            review_recommendation(
                c,
                users["admin"],
                rec,
                0,
                "accepted",
                reason="Admin role is not technical authority",
            )
        with pytest.raises(ValueError, match="Stale"):
            review_recommendation(c, users["engineer"], rec, 9, "accepted", reason="Stale review")
        assert (
            review_recommendation(
                c, users["engineer"], rec, 0, "accepted", reason="Approve investigation only"
            )
            == 1
        )
        work = draft_work_order(
            c, users["planner"], rec, 1, reason="Draft known diagnostic procedure"
        )
        assert work == draft_work_order(
            c, users["planner"], rec, 1, reason="Idempotent planner retry"
        )
        with pytest.raises(DBAPIError, match="transition service"), c.begin_nested():
            c.execute(sa.update(WorkOrder).where(WorkOrder.id == work).values(state="released"))
        with pytest.raises(Forbidden):
            approve_scope(
                c,
                users["planner"],
                work,
                0,
                [ids["procedure"]],
                reason="Planner lacks technical permission",
            )
        assert (
            approve_scope(
                c,
                users["engineer"],
                work,
                0,
                [ids["procedure"]],
                reason="Approve immutable parts/skills/duration",
            )
            == 1
        )
        assert (
            transition(
                c, users["planner"], work, 1, "schedule_proposed", reason="Propose human plan"
            )
            == 2
        )
        with pytest.raises(ValueError, match="bound to scope"):
            transition(
                c,
                users["planner"],
                work,
                2,
                "schedule_approved",
                reason="Wrong scope",
                plan_approval={"scope_hash": "a" * 64, "plan_reference": "demo", "plan_version": 1},
            )
        scope = c.scalar(sa.select(WorkOrder.technical_scope).where(WorkOrder.id == work))
        assert scope["tasks"][0]["part_requirements"] == [
            {"part_id": str(ids["part"]), "quantity": 1}
        ]
        assert (
            transition(
                c,
                users["planner"],
                work,
                2,
                "schedule_approved",
                reason="Planner independently approves plan",
                plan_approval={
                    "scope_hash": scope["scope_hash"],
                    "plan_reference": "fictional-human-plan-1",
                    "plan_version": 1,
                },
            )
            == 3
        )
        assert (
            transition(
                c, users["technician"], work, 3, "executing", reason="Execute approved scope"
            )
            == 4
        )
        task = c.scalar(sa.select(MaintenanceTask.id).where(MaintenanceTask.work_order_id == work))
        with pytest.raises(ValueError, match="complete every task"):
            transition(
                c, users["technician"], work, 4, "inspection_pending", reason="No completion yet"
            )
        assert (
            task_transition(c, users["technician"], task, 0, "executing", reason="Begin task") == 1
        )
        assert (
            task_transition(
                c,
                users["technician"],
                task,
                1,
                "completed",
                reason="Procedure completed; request inspection",
            )
            == 2
        )
        assert (
            transition(
                c,
                users["technician"],
                work,
                4,
                "inspection_pending",
                reason="Technician cannot release",
            )
            == 5
        )
        with pytest.raises(Forbidden):
            transition(
                c, users["technician"], work, 5, "released", reason="Forbidden technician release"
            )
        with pytest.raises(ValueError, match="Independent passing inspection"):
            transition(c, users["inspector"], work, 5, "released", reason="Missing inspection")
        record_inspection(
            c, users["inspector"], task, 2, "fail", reason="Independent failed inspection"
        )
        assert (
            transition(
                c,
                users["inspector"],
                work,
                5,
                "held",
                reason="Hold for unresolved inspection finding",
            )
            == 6
        )
        with pytest.raises(ValueError, match="Stale"):
            record_inspection(c, users["inspector"], task, 1, "pass", reason="Stale task evidence")
        record_inspection(
            c,
            users["inspector"],
            task,
            2,
            "pass",
            reason="Independent repeat inspection confirms satisfactory procedure",
        )
        assert (
            transition(
                c,
                users["technician"],
                work,
                6,
                "executing",
                reason="Resume held approved scope after review",
            )
            == 7
        )
        assert (
            transition(
                c,
                users["technician"],
                work,
                7,
                "inspection_pending",
                reason="Present completed scope for release review",
            )
            == 8
        )
        assert (
            transition(
                c,
                users["inspector"],
                work,
                8,
                "released",
                reason="Record explicit human release after passing inspection",
            )
            == 9
        )
        assert (
            transition(c, users["inspector"], work, 9, "closed", reason="Close released work") == 10
        )
    with migration.connect() as c:
        assert c.scalar(sa.select(WorkOrder.state).where(WorkOrder.id == work)) == "closed"
        audit = (
            c.execute(sa.select(AuditEvent.__table__).where(AuditEvent.action.like("workflow.%")))
            .mappings()
            .all()
        )
        events = c.scalar(
            sa.select(sa.func.count())
            .select_from(EventOutbox)
            .where(EventOutbox.kind.like("workflow.%"))
        )
        assert len(audit) == events == 16
        assert all(r["actor_id"] and r["reason"] and "after" in r["versions"] for r in audit)
        assert c.scalar(sa.select(WorkOrder.version).where(WorkOrder.id == work)) == 10


def test_rejection_rollback_and_approved_resource_binding(workflow_fixture):
    application, migration, ids, users = workflow_fixture
    with application.begin() as c:
        review_recommendation(
            c,
            users["engineer"],
            ids["rejected"],
            0,
            "rejected",
            reason="Insufficient procedure evidence",
        )
        with pytest.raises(ValueError, match="Accepted engineering"):
            draft_work_order(
                c,
                users["planner"],
                ids["rejected"],
                1,
                reason="Rejected evidence cannot create work",
            )
        with pytest.raises(ValueError, match="Reason"):
            review_recommendation(
                c, users["engineer"], ids["recommendation"], 0, "accepted", reason=" "
            )
        assert (
            c.scalar(
                sa.select(Recommendation.state).where(Recommendation.id == ids["recommendation"])
            )
            == "engineering_review"
        )
        review_recommendation(
            c, users["engineer"], ids["recommendation"], 0, "accepted", reason="Review approved"
        )
        work = draft_work_order(c, users["planner"], ids["recommendation"], 1, reason="Draft")
        approve_scope(c, users["engineer"], work, 0, [ids["procedure"]], reason="Scope approved")
    with migration.begin() as c:
        c.execute(
            sa.update(MaintenanceTask)
            .where(MaintenanceTask.work_order_id == work)
            .values(duration_slots=99)
        )
    with application.begin() as c:
        with pytest.raises(ValueError, match="approved procedure revision"):
            transition(
                c, users["planner"], work, 1, "schedule_proposed", reason="Tampered duration"
            )
        assert c.scalar(sa.select(WorkOrder.state).where(WorkOrder.id == work)) == "planner_draft"
