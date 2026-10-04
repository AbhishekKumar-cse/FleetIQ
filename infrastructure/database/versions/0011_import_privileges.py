"""Least-privilege ingestion and versioned reference/stock import writes."""

from alembic import op

revision = "0011_import_privileges"
down_revision = "0010a_auth_privileges"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "GRANT INSERT ON source_event_receipt,sensor_reading,import_batch,quarantine,stock_movement TO fleetiq_app"
    )
    op.execute(
        "GRANT UPDATE (state,total_rows,accepted_rows,quarantined_rows,summary) ON import_batch TO fleetiq_app"
    )
    op.execute("GRANT UPDATE (configuration) ON aircraft_type TO fleetiq_app")
    op.execute("GRANT UPDATE (on_hand,version) ON inventory TO fleetiq_app")


def downgrade():
    op.execute(
        "REVOKE INSERT ON source_event_receipt,sensor_reading,import_batch,quarantine,stock_movement FROM fleetiq_app"
    )
    op.execute(
        "REVOKE UPDATE (state,total_rows,accepted_rows,quarantined_rows,summary) ON import_batch FROM fleetiq_app"
    )
    op.execute("REVOKE UPDATE (configuration) ON aircraft_type FROM fleetiq_app")
    op.execute("REVOKE UPDATE (on_hand,version) ON inventory FROM fleetiq_app")
