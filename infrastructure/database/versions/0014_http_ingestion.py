"""Frozen ingestion schema."""

from alembic import op

revision = "0014_http_ingestion"
down_revision = "0013a_revision_guard"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "\nCREATE TABLE ingestion_receipt (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tsource_id UUID NOT NULL, \n\trequest_key TEXT NOT NULL, \n\tinput_hash TEXT NOT NULL, \n\tresponse JSONB NOT NULL, \n\troute TEXT NOT NULL, \n\tCONSTRAINT pk_ingestion_receipt PRIMARY KEY (id), \n\tCONSTRAINT fk_ingestion_receipt_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_ingestion_receipt_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_ingestion_receipt_source_id FOREIGN KEY(organization_id, source_id) REFERENCES source (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_ingestion_receipt_rule_0 CHECK (input_hash ~ '^[0-9a-f]{64}$'), \n\tCONSTRAINT ck_ingestion_receipt_rule_1 CHECK (length(request_key) BETWEEN 1 AND 128), \n\tCONSTRAINT uq_ingestion_receipt_key_0 UNIQUE (organization_id, source_id, route, request_key)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE source_access (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tsource_id UUID NOT NULL, \n\tuser_id UUID NOT NULL, \n\ttoken_hash TEXT NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\trevoked BOOLEAN DEFAULT false NOT NULL, \n\tCONSTRAINT pk_source_access PRIMARY KEY (id), \n\tCONSTRAINT fk_source_access_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_source_access_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_source_access_source_id FOREIGN KEY(organization_id, source_id) REFERENCES source (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_source_access_user_id FOREIGN KEY(organization_id, user_id) REFERENCES app_user (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_source_access_rule_0 CHECK (token_hash ~ '^[0-9a-f]{64}$')\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE source_watermark (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tsource_id UUID NOT NULL, \n\tcomponent_id UUID NOT NULL, \n\tlatest_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_source_watermark PRIMARY KEY (id), \n\tCONSTRAINT fk_source_watermark_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_source_watermark_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_source_watermark_source_id FOREIGN KEY(organization_id, source_id) REFERENCES source (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_source_watermark_component_id FOREIGN KEY(organization_id, component_id) REFERENCES component (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_source_watermark_key_0 UNIQUE (organization_id, source_id, component_id)\n)\n\n"
    )
    op.execute(
        "CREATE TRIGGER immutable_ingestion_receipt BEFORE UPDATE OR DELETE ON ingestion_receipt FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
    op.execute(
        "GRANT SELECT ON ingestion_receipt,source_access,source_watermark TO fleetiq_app, fleetiq_worker"
    )
    op.execute("GRANT INSERT ON ingestion_receipt,source_watermark TO fleetiq_app")
    op.execute("GRANT UPDATE(latest_at) ON source_watermark TO fleetiq_app")
    op.execute(
        "GRANT INSERT ON source_event_receipt,sensor_reading,import_batch,quarantine TO fleetiq_worker"
    )
    op.execute(
        "GRANT UPDATE(state,total_rows,accepted_rows,quarantined_rows,summary) ON import_batch TO fleetiq_worker"
    )


def downgrade():
    op.execute("DROP TABLE source_watermark")
    op.execute("DROP TABLE source_access")
    op.execute("DROP TABLE ingestion_receipt")
