"""Serialize app coalescing with claims and validate same-series supersession."""

from alembic import op

revision = "0013a_revision_guard"
down_revision = "0013_backfill"
branch_labels = None
depends_on = None


def upgrade():
    # A column grant permits SELECT FOR UPDATE without granting mutation of job inputs/state.
    op.execute("GRANT UPDATE(available_at) ON job TO fleetiq_app")
    op.execute("""CREATE FUNCTION fleetiq_validate_revision() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE matched boolean;
    BEGIN
    IF NEW.supersedes_id IS NOT NULL THEN
      IF NEW.source_cutoff IS NULL OR NEW.id=NEW.supersedes_id THEN RAISE EXCEPTION 'revision cutoff required'; END IF;
      EXECUTE format('SELECT EXISTS(SELECT 1 FROM %I p WHERE p.id=$1 AND p.organization_id=$2 AND p.component_id=$3 AND p.as_of=$4 AND p.track=$5 AND p.feature_version=$6 AND p.input_hash<>$7)',TG_TABLE_NAME)
      INTO matched USING NEW.supersedes_id,NEW.organization_id,NEW.component_id,NEW.as_of,NEW.track,NEW.feature_version,NEW.input_hash;
      IF NOT matched THEN RAISE EXCEPTION 'revision series mismatch'; END IF;
    END IF; RETURN NEW; END $$""")
    for table in ("feature_snapshot", "prediction"):
        op.execute(
            f"CREATE TRIGGER validate_revision BEFORE INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION fleetiq_validate_revision()"
        )


def downgrade():
    for table in ("feature_snapshot", "prediction"):
        op.execute(f"DROP TRIGGER validate_revision ON {table}")
    op.execute("DROP FUNCTION fleetiq_validate_revision()")
    op.execute("REVOKE UPDATE(available_at) ON job FROM fleetiq_app")
