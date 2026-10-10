"""Guarded stock transactions and serial-specific reservations."""

from alembic import op

revision = "0018_stock_service"
down_revision = "0017_workflow_service"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE part_reservation ADD COLUMN component_id UUID")
    op.execute(
        "ALTER TABLE part_reservation ADD CONSTRAINT fk_reservation_serial FOREIGN KEY(organization_id,component_id) REFERENCES component(organization_id,id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_reserved_serial ON part_reservation(organization_id,component_id) WHERE state='reserved' AND component_id IS NOT NULL"
    )
    op.execute("""CREATE TABLE stock_operation (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(), organization_id UUID NOT NULL REFERENCES organization(id),
        idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL, result JSONB NOT NULL,
        actor_id UUID NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE(organization_id,idempotency_key),
        FOREIGN KEY(organization_id,actor_id) REFERENCES app_user(organization_id,id))""")
    op.execute(
        "CREATE TRIGGER immutable_stock_operation BEFORE UPDATE OR DELETE ON stock_operation FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
    op.execute("""CREATE FUNCTION fleetiq_stock_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN IF current_user='fleetiq_app' AND COALESCE(current_setting('fleetiq.stock_mutation',true),'')<>'allowed'
    THEN RAISE EXCEPTION 'stock mutation requires stock service'; END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW; END $$""")
    for table in (
        "inventory",
        "part_reservation",
        "serialized_stock",
        "stock_movement",
        "task_part",
        "stock_operation",
    ):
        op.execute(
            f"CREATE TRIGGER stock_guard BEFORE INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION fleetiq_stock_guard()"
        )
    op.execute("GRANT SELECT,INSERT ON stock_operation TO fleetiq_app")
    op.execute(
        "GRANT INSERT ON inventory,part_reservation,serialized_stock,stock_movement,task_part TO fleetiq_app"
    )
    op.execute("GRANT UPDATE(on_hand,reserved,quarantined,version) ON inventory TO fleetiq_app")
    op.execute("GRANT UPDATE(state,version,component_id) ON part_reservation TO fleetiq_app")
    op.execute("GRANT UPDATE(inventory_id),DELETE ON serialized_stock TO fleetiq_app")


def downgrade():
    for table in (
        "inventory",
        "part_reservation",
        "serialized_stock",
        "stock_movement",
        "task_part",
        "stock_operation",
    ):
        op.execute(f"DROP TRIGGER stock_guard ON {table}")
    op.execute("DROP FUNCTION fleetiq_stock_guard()")
    op.execute("DROP TABLE stock_operation")
    op.execute(
        "REVOKE INSERT ON inventory,part_reservation,serialized_stock,stock_movement,task_part FROM fleetiq_app"
    )
    op.execute("REVOKE UPDATE(on_hand,reserved,quarantined,version) ON inventory FROM fleetiq_app")
    op.execute("REVOKE UPDATE(state,version,component_id) ON part_reservation FROM fleetiq_app")
    op.execute("REVOKE UPDATE(inventory_id),DELETE ON serialized_stock FROM fleetiq_app")
    op.execute("DROP INDEX uq_reserved_serial")
    op.execute("ALTER TABLE part_reservation DROP CONSTRAINT fk_reservation_serial")
    op.execute("ALTER TABLE part_reservation DROP COLUMN component_id")
