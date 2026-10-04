"""Frozen backfill schema."""

from alembic import op

revision = "0013_backfill"
down_revision = "0012_worker_leases"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE feature_snapshot ADD COLUMN source_cutoff timestamptz, ADD COLUMN supersedes_id uuid"
    )
    op.execute(
        "ALTER TABLE feature_snapshot ADD CONSTRAINT fk_feature_snapshot_supersedes_id FOREIGN KEY(organization_id,supersedes_id) REFERENCES feature_snapshot(organization_id,id) ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE prediction ADD COLUMN source_cutoff timestamptz, ADD COLUMN supersedes_id uuid"
    )
    op.execute(
        "ALTER TABLE prediction ADD CONSTRAINT fk_prediction_supersedes_id FOREIGN KEY(organization_id,supersedes_id) REFERENCES prediction(organization_id,id) ON DELETE RESTRICT"
    )
    op.execute(
        "\nCREATE TABLE backfill_request (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tcomponent_id UUID NOT NULL, \n\tas_of TIMESTAMP WITH TIME ZONE NOT NULL, \n\tsource_cutoff TIMESTAMP WITH TIME ZONE NOT NULL, \n\tinput_hash TEXT NOT NULL, \n\thistorical BOOLEAN NOT NULL, \n\tactor_id UUID NOT NULL, \n\tCONSTRAINT pk_backfill_request PRIMARY KEY (id), \n\tCONSTRAINT fk_backfill_request_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_backfill_request_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_backfill_request_component_id FOREIGN KEY(organization_id, component_id) REFERENCES component (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_backfill_request_actor_id FOREIGN KEY(organization_id, actor_id) REFERENCES app_user (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_backfill_request_rule_0 CHECK (input_hash ~ '^[0-9a-f]{64}$'), \n\tCONSTRAINT uq_backfill_request_key_0 UNIQUE (organization_id, component_id, as_of, input_hash, historical)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE assessment_cursor (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tcomponent_id UUID NOT NULL, \n\trequest_id UUID NOT NULL, \n\tjob_id UUID NOT NULL, \n\tCONSTRAINT pk_assessment_cursor PRIMARY KEY (id), \n\tCONSTRAINT fk_assessment_cursor_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_assessment_cursor_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_assessment_cursor_component_id FOREIGN KEY(organization_id, component_id) REFERENCES component (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_assessment_cursor_request_id FOREIGN KEY(organization_id, request_id) REFERENCES backfill_request (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_assessment_cursor_job_id FOREIGN KEY(organization_id, job_id) REFERENCES job (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_assessment_cursor_key_0 UNIQUE (organization_id, component_id)\n)\n\n"
    )
    op.execute(
        "CREATE TRIGGER immutable_backfill_request BEFORE UPDATE OR DELETE ON backfill_request FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
    op.execute("GRANT SELECT ON backfill_request,assessment_cursor TO fleetiq_app, fleetiq_worker")
    op.execute("GRANT INSERT ON backfill_request,assessment_cursor TO fleetiq_app")
    op.execute("GRANT UPDATE(request_id,job_id) ON assessment_cursor TO fleetiq_app")
    op.execute("GRANT INSERT ON feature_snapshot,prediction TO fleetiq_worker")


def downgrade():
    op.execute("DROP TABLE assessment_cursor")
    op.execute("DROP TABLE backfill_request")
    op.execute("ALTER TABLE prediction DROP COLUMN supersedes_id, DROP COLUMN source_cutoff")
    op.execute("ALTER TABLE feature_snapshot DROP COLUMN supersedes_id, DROP COLUMN source_cutoff")
