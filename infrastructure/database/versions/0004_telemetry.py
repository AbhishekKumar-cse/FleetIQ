"""Frozen telemetry schema."""

from alembic import op

revision = "0004_telemetry"
down_revision = "0003_components"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "\nCREATE TABLE source (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tcode TEXT NOT NULL, \n\tsource_kind TEXT NOT NULL, \n\tschema_version TEXT NOT NULL, \n\tCONSTRAINT pk_source PRIMARY KEY (id), \n\tCONSTRAINT fk_source_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_source_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT ck_source_rule_0 CHECK (source_kind IN ('nasa_cmapss','synthetic_engine')), \n\tCONSTRAINT uq_source_key_0 UNIQUE (organization_id, code)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE sensor (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tcomponent_id UUID NOT NULL, \n\tsource_id UUID NOT NULL, \n\tchannel_code TEXT NOT NULL, \n\tunit TEXT NOT NULL, \n\tessential BOOLEAN DEFAULT true NOT NULL, \n\tCONSTRAINT pk_sensor PRIMARY KEY (id), \n\tCONSTRAINT fk_sensor_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_sensor_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_sensor_component_id FOREIGN KEY(organization_id, component_id) REFERENCES component (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_sensor_source_id FOREIGN KEY(organization_id, source_id) REFERENCES source (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_sensor_key_0 UNIQUE (organization_id, component_id, channel_code, source_id), \n\tCONSTRAINT uq_sensor_key_1 UNIQUE (organization_id, id, source_id)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE source_event_receipt (\n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tsource_id UUID NOT NULL, \n\tevent_id UUID NOT NULL, \n\tpayload_sha256 TEXT NOT NULL, \n\treceived_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_source_event_receipt PRIMARY KEY (organization_id, source_id, event_id), \n\tCONSTRAINT fk_source_event_receipt_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_source_event_receipt_source_id FOREIGN KEY(organization_id, source_id) REFERENCES source (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_source_event_receipt_rule_0 CHECK (payload_sha256 ~ '^[0-9a-f]{64}$')\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE sensor_reading (\n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tsensor_id UUID NOT NULL, \n\tsource_id UUID NOT NULL, \n\tevent_id UUID NOT NULL, \n\tinstallation_id UUID NOT NULL, \n\tobserved_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tingested_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tvalue DOUBLE PRECISION, \n\traw_value TEXT, \n\tcanonical_unit TEXT NOT NULL, \n\traw_unit TEXT NOT NULL, \n\tquality TEXT NOT NULL, \n\tCONSTRAINT pk_sensor_reading PRIMARY KEY (sensor_id, observed_at, event_id), \n\tCONSTRAINT fk_sensor_reading_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_sensor_reading_sensor_id FOREIGN KEY(organization_id, sensor_id) REFERENCES sensor (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_sensor_reading_source_id FOREIGN KEY(organization_id, source_id) REFERENCES source (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_sensor_reading_installation_id FOREIGN KEY(organization_id, installation_id) REFERENCES installation (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_sensor_reading_rule_0 CHECK (quality IN ('valid','flagged','missing','invalid')), \n\tCONSTRAINT ck_sensor_reading_rule_1 CHECK (value IS NULL OR (value > '-Infinity'::float8 AND value < 'Infinity'::float8)), \n\tCONSTRAINT ck_sensor_reading_rule_2 CHECK (quality <> 'valid' OR value IS NOT NULL), \n\tCONSTRAINT fk_reading_receipt FOREIGN KEY(organization_id, source_id, event_id) REFERENCES source_event_receipt (organization_id, source_id, event_id), \n\tCONSTRAINT fk_reading_sensor_source FOREIGN KEY(organization_id, sensor_id, source_id) REFERENCES sensor (organization_id, id, source_id)\n)\n\n"
    )
    op.execute(
        "SELECT create_hypertable('sensor_reading', by_range('observed_at'), create_default_indexes => false)"
    )
    op.execute(
        "CREATE INDEX ix_reading_org_sensor_time ON sensor_reading (organization_id,sensor_id,observed_at)"
    )
    op.execute(
        "CREATE FUNCTION fleetiq_validate_reading() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN\n       IF NOT EXISTS(SELECT 1 FROM installation i JOIN sensor s ON s.component_id=i.component_id AND s.organization_id=i.organization_id\n         WHERE i.id=NEW.installation_id AND s.id=NEW.sensor_id AND i.organization_id=NEW.organization_id\n         AND NEW.observed_at >= i.installed_at AND (i.removed_at IS NULL OR NEW.observed_at < i.removed_at))\n       THEN RAISE EXCEPTION 'reading outside installed component interval'; END IF; RETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER validate_reading BEFORE INSERT OR UPDATE ON sensor_reading FOR EACH ROW EXECUTE FUNCTION fleetiq_validate_reading()"
    )
    op.execute(
        "GRANT SELECT ON source,sensor,source_event_receipt,sensor_reading TO fleetiq_app, fleetiq_worker"
    )


def downgrade():
    op.execute("DROP TABLE sensor_reading")
    op.execute("DROP FUNCTION fleetiq_validate_reading()")
    op.execute("DROP TABLE source_event_receipt")
    op.execute("DROP TABLE sensor")
    op.execute("DROP TABLE source")
