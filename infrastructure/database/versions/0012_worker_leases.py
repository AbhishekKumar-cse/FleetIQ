"""Frozen worker ready times, retry outcomes and unique immutable result schema."""

from alembic import op

revision = "0012_worker_leases"
down_revision = "0011_import_privileges"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE job ADD COLUMN available_at timestamptz NOT NULL DEFAULT now()")
    op.execute("ALTER TABLE job ALTER COLUMN max_attempts SET DEFAULT 6")
    op.execute(
        "ALTER TABLE job DROP CONSTRAINT ck_job_rule_1, DROP CONSTRAINT ck_job_rule_2, DROP CONSTRAINT ck_job_rule_5"
    )
    op.execute(
        "UPDATE job SET max_attempts=6 WHERE max_attempts=3 AND state='pending' AND attempt=0"
    )
    op.execute(
        "ALTER TABLE job ADD CONSTRAINT ck_job_rule_1 CHECK (state IN ('pending','running','completed','failed','cancelled','dead_letter','unsupported')), ADD CONSTRAINT ck_job_rule_2 CHECK (max_attempts BETWEEN 1 AND 6 AND attempt BETWEEN 0 AND max_attempts), ADD CONSTRAINT ck_job_rule_5 CHECK (state NOT IN ('failed','dead_letter','unsupported') OR error IS NOT NULL)"
    )
    op.execute("CREATE INDEX ix_job_ready ON job(state,available_at,lease_expires_at)")
    op.execute("""CREATE TABLE job_result (
        id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
        organization_id uuid NOT NULL REFERENCES organization(id) ON DELETE RESTRICT,
        created_at timestamptz NOT NULL DEFAULT now(), job_id uuid NOT NULL,
        result_key uuid NOT NULL, attempt bigint NOT NULL, state text NOT NULL, payload jsonb NOT NULL,
        CONSTRAINT uq_job_result_org_id UNIQUE(organization_id,id),
        CONSTRAINT fk_job_result_job_id FOREIGN KEY(organization_id,job_id) REFERENCES job(organization_id,id) ON DELETE RESTRICT,
        CONSTRAINT uq_job_result_key_0 UNIQUE(organization_id,job_id),
        CONSTRAINT uq_job_result_key_1 UNIQUE(organization_id,result_key),
        CONSTRAINT ck_job_result_rule_0 CHECK(attempt BETWEEN 1 AND 6),
        CONSTRAINT ck_job_result_rule_1 CHECK(state IN ('completed','unsupported')),
        CONSTRAINT ck_job_result_rule_2 CHECK(jsonb_typeof(payload)='object')
    )""")
    op.execute(
        "CREATE TRIGGER immutable_job_result BEFORE UPDATE OR DELETE ON job_result FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
    op.execute("GRANT SELECT ON job_result TO fleetiq_app,fleetiq_worker")
    op.execute("GRANT INSERT ON job_result TO fleetiq_worker")
    op.execute("GRANT UPDATE (available_at) ON job TO fleetiq_worker")


def downgrade():
    # Reject loss of new terminal evidence rather than rewrite it during a test downgrade.
    op.execute("DROP TABLE job_result")
    op.execute("DROP INDEX ix_job_ready")
    op.execute(
        "ALTER TABLE job DROP CONSTRAINT ck_job_rule_1, DROP CONSTRAINT ck_job_rule_2, DROP CONSTRAINT ck_job_rule_5"
    )
    op.execute(
        "ALTER TABLE job ADD CONSTRAINT ck_job_rule_1 CHECK (state IN ('pending','running','completed','failed','cancelled')), ADD CONSTRAINT ck_job_rule_2 CHECK(max_attempts BETWEEN 1 AND 10 AND attempt BETWEEN 0 AND max_attempts), ADD CONSTRAINT ck_job_rule_5 CHECK(state<>'failed' OR error IS NOT NULL)"
    )
    op.execute("ALTER TABLE job ALTER COLUMN max_attempts SET DEFAULT 3")
    op.execute("ALTER TABLE job DROP COLUMN available_at")
