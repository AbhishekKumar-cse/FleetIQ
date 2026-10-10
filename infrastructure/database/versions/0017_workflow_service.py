"""Runtime workflow writes require the transition service and retain immutable evidence."""

from alembic import op

revision = "0017_workflow_service"
down_revision = "0016_twin"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE UNIQUE INDEX uq_work_recommendation ON work_order(organization_id,recommendation_id)"
    )
    op.execute("""CREATE FUNCTION fleetiq_workflow_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN IF current_user='fleetiq_app' AND COALESCE(current_setting('fleetiq.workflow_mutation',true),'')<>'allowed'
    THEN RAISE EXCEPTION 'workflow mutation requires transition service'; END IF; RETURN NEW; END $$""")
    for table in (
        "recommendation",
        "recommendation_evidence",
        "work_order",
        "maintenance_task",
        "approval",
        "maintenance_event",
        "inspection",
    ):
        op.execute(
            f"CREATE TRIGGER workflow_guard BEFORE INSERT OR UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION fleetiq_workflow_guard()"
        )
    op.execute(
        "GRANT INSERT ON recommendation,recommendation_evidence,work_order,maintenance_task,approval,maintenance_event,inspection TO fleetiq_app"
    )
    op.execute("GRANT UPDATE(state,version) ON recommendation,maintenance_task TO fleetiq_app")
    op.execute(
        "GRANT UPDATE(state,version,technical_scope,plan_approval) ON work_order TO fleetiq_app"
    )


def downgrade():
    op.execute(
        "REVOKE INSERT ON recommendation,recommendation_evidence,work_order,maintenance_task,approval,maintenance_event,inspection FROM fleetiq_app"
    )
    op.execute("REVOKE UPDATE(state,version) ON recommendation,maintenance_task FROM fleetiq_app")
    op.execute(
        "REVOKE UPDATE(state,version,technical_scope,plan_approval) ON work_order FROM fleetiq_app"
    )
    for table in (
        "recommendation",
        "recommendation_evidence",
        "work_order",
        "maintenance_task",
        "approval",
        "maintenance_event",
        "inspection",
    ):
        op.execute(f"DROP TRIGGER workflow_guard ON {table}")
    op.execute("DROP FUNCTION fleetiq_workflow_guard()")
    op.execute("DROP INDEX uq_work_recommendation")
