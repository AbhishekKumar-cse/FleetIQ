"""Allow only explanation follow-up jobs from the worker role."""

from alembic import op

revision = "0015_prediction_jobs"
down_revision = "0014_http_ingestion"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE FUNCTION fleetiq_worker_job_insert() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN IF current_user='fleetiq_worker' AND NEW.kind<>'prediction.explain'
    THEN RAISE EXCEPTION 'worker may enqueue only prediction explanations'; END IF;
    RETURN NEW; END $$""")
    op.execute(
        "CREATE TRIGGER worker_job_insert BEFORE INSERT ON job FOR EACH ROW EXECUTE FUNCTION fleetiq_worker_job_insert()"
    )
    op.execute("GRANT INSERT ON job TO fleetiq_worker")


def downgrade():
    op.execute("REVOKE INSERT ON job FROM fleetiq_worker")
    op.execute("DROP TRIGGER worker_job_insert ON job")
    op.execute("DROP FUNCTION fleetiq_worker_job_insert()")
