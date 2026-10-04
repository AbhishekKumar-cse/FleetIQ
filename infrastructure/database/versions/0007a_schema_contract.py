"""Complete history/work lookup indexes and explicit immutable approval target identity."""

from alembic import op

revision = "0007a_schema_contract"
down_revision = "0007_inventory"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE INDEX ix_flight_aircraft_time ON flight(organization_id,aircraft_id,start)")
    op.execute(
        "CREATE INDEX ix_work_aircraft_state ON work_order(organization_id,aircraft_id,state)"
    )
    op.execute(
        "CREATE INDEX ix_task_work_state ON maintenance_task(organization_id,work_order_id,state)"
    )
    # Generated targets cannot drift from their scoped work-order FK and require no evidence rewrite.
    op.execute(
        "ALTER TABLE approval ADD COLUMN target_kind text GENERATED ALWAYS AS ('work_order'::text) STORED NOT NULL"
    )
    op.execute(
        "ALTER TABLE approval ADD COLUMN target_id uuid GENERATED ALWAYS AS (work_order_id) STORED NOT NULL"
    )


def downgrade():
    op.execute("ALTER TABLE approval DROP COLUMN target_id")
    op.execute("ALTER TABLE approval DROP COLUMN target_kind")
    op.execute("DROP INDEX ix_task_work_state")
    op.execute("DROP INDEX ix_work_aircraft_state")
    op.execute("DROP INDEX ix_flight_aircraft_time")
