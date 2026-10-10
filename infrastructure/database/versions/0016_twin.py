"""Frozen twin schema."""

from alembic import op

revision = "0016_twin"
down_revision = "0015_prediction_jobs"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "\nCREATE TABLE twin_event (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\taircraft_id UUID NOT NULL, \n\tkind TEXT NOT NULL, \n\toccurred_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\trecorded_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\trevision BIGINT NOT NULL, \n\tpayload JSONB NOT NULL, \n\tactor_id UUID NOT NULL, \n\tsupersedes_id UUID, \n\tCONSTRAINT pk_twin_event PRIMARY KEY (id), \n\tCONSTRAINT fk_twin_event_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_twin_event_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_twin_event_aircraft_id FOREIGN KEY(organization_id, aircraft_id) REFERENCES aircraft (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_twin_event_actor_id FOREIGN KEY(organization_id, actor_id) REFERENCES app_user (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_twin_event_supersedes_id FOREIGN KEY(organization_id, supersedes_id) REFERENCES twin_event (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_twin_event_rule_0 CHECK (kind IN ('installed','removed','telemetry','prediction','maintenance','status')), \n\tCONSTRAINT ck_twin_event_rule_1 CHECK (recorded_at>=occurred_at AND revision>0), \n\tCONSTRAINT ck_twin_event_rule_2 CHECK (supersedes_id IS NULL OR supersedes_id<>id), \n\tCONSTRAINT uq_twin_event_key_0 UNIQUE (organization_id, aircraft_id, revision)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE twin_snapshot (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\taircraft_id UUID NOT NULL, \n\tas_of TIMESTAMP WITH TIME ZONE NOT NULL, \n\tsource_cutoff TIMESTAMP WITH TIME ZONE NOT NULL, \n\trevision BIGINT NOT NULL, \n\tprojection_version TEXT NOT NULL, \n\tstate JSONB NOT NULL, \n\tstate_hash TEXT NOT NULL, \n\tevents_hash TEXT NOT NULL, \n\tCONSTRAINT pk_twin_snapshot PRIMARY KEY (id), \n\tCONSTRAINT fk_twin_snapshot_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_twin_snapshot_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_twin_snapshot_aircraft_id FOREIGN KEY(organization_id, aircraft_id) REFERENCES aircraft (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_twin_snapshot_rule_0 CHECK (source_cutoff<=as_of AND revision>=0), \n\tCONSTRAINT ck_twin_snapshot_rule_1 CHECK (state_hash ~ '^[0-9a-f]{64}$'), \n\tCONSTRAINT ck_twin_snapshot_rule_2 CHECK (events_hash ~ '^[0-9a-f]{64}$'), \n\tCONSTRAINT uq_twin_snapshot_key_0 UNIQUE (organization_id, aircraft_id, as_of, source_cutoff, projection_version, events_hash)\n)\n\n"
    )
    op.execute(
        "CREATE TRIGGER immutable_twin_event BEFORE UPDATE OR DELETE ON twin_event FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
    op.execute(
        "CREATE TRIGGER immutable_twin_snapshot BEFORE UPDATE OR DELETE ON twin_snapshot FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
    op.execute("GRANT SELECT ON twin_event,twin_snapshot TO fleetiq_app, fleetiq_worker")
    op.execute("GRANT INSERT ON twin_event,twin_snapshot TO fleetiq_app")


def downgrade():
    op.execute("DROP TABLE twin_snapshot")
    op.execute("DROP TABLE twin_event")
