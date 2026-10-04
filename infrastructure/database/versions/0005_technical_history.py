"""Frozen history schema."""

from alembic import op

revision = "0005_technical_history"
down_revision = "0004_telemetry"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "\nCREATE TABLE failure_event (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tcomponent_id UUID NOT NULL, \n\tobserved_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tconfirmed_at TIMESTAMP WITH TIME ZONE, \n\trecorded_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tmode TEXT NOT NULL, \n\tconfirmed_by UUID, \n\tconfirmation_evidence TEXT, \n\teligibility TEXT NOT NULL, \n\tCONSTRAINT pk_failure_event PRIMARY KEY (id), \n\tCONSTRAINT fk_failure_event_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_failure_event_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_failure_event_component_id FOREIGN KEY(organization_id, component_id) REFERENCES component (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_failure_event_rule_0 CHECK (recorded_at >= observed_at), \n\tCONSTRAINT ck_failure_event_rule_1 CHECK (confirmed_at IS NULL OR (confirmed_at >= observed_at AND recorded_at >= confirmed_at)), \n\tCONSTRAINT ck_failure_event_rule_2 CHECK (eligibility IN ('unconfirmed','confirmed','censored','intervened')), \n\tCONSTRAINT ck_failure_event_rule_3 CHECK (eligibility <> 'confirmed' OR (confirmed_at IS NOT NULL AND confirmed_by IS NOT NULL AND confirmation_evidence IS NOT NULL))\n)\n\n"
    )
    op.execute(
        '\nCREATE TABLE flight (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\taircraft_id UUID NOT NULL, \n\tstart TIMESTAMP WITH TIME ZONE NOT NULL, \n\t"end" TIMESTAMP WITH TIME ZONE NOT NULL, \n\tduration_hours NUMERIC(18, 6) NOT NULL, \n\tflight_cycles BIGINT NOT NULL, \n\tlanding_cycles BIGINT NOT NULL, \n\tcontext JSONB, \n\tCONSTRAINT pk_flight PRIMARY KEY (id), \n\tCONSTRAINT fk_flight_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_flight_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_flight_aircraft_id FOREIGN KEY(organization_id, aircraft_id) REFERENCES aircraft (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_flight_rule_0 CHECK ("end" > start), \n\tCONSTRAINT ck_flight_rule_1 CHECK (duration_hours > 0 AND flight_cycles >= 0 AND landing_cycles >= 0)\n)\n\n'
    )
    op.execute(
        "\nCREATE TABLE maintenance_event (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\taircraft_id UUID NOT NULL, \n\tcomponent_id UUID NOT NULL, \n\ttask_id UUID, \n\toccurred_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\trecorded_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\taction TEXT NOT NULL, \n\tactor_id UUID NOT NULL, \n\tsupersedes_id UUID, \n\tCONSTRAINT pk_maintenance_event PRIMARY KEY (id), \n\tCONSTRAINT fk_maintenance_event_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_maintenance_event_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_maintenance_event_aircraft_id FOREIGN KEY(organization_id, aircraft_id) REFERENCES aircraft (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_maintenance_event_component_id FOREIGN KEY(organization_id, component_id) REFERENCES component (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_maintenance_event_supersedes_id FOREIGN KEY(organization_id, supersedes_id) REFERENCES maintenance_event (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_maintenance_event_rule_0 CHECK (recorded_at >= occurred_at), \n\tCONSTRAINT ck_maintenance_event_rule_1 CHECK (supersedes_id IS NULL OR supersedes_id <> id)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE inspection (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tmaintenance_event_id UUID NOT NULL, \n\ttask_id UUID, \n\tprocedure_revision_id UUID, \n\tresult TEXT NOT NULL, \n\tinspector_id UUID NOT NULL, \n\tcompleted_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_inspection PRIMARY KEY (id), \n\tCONSTRAINT fk_inspection_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_inspection_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_inspection_maintenance_event_id FOREIGN KEY(organization_id, maintenance_event_id) REFERENCES maintenance_event (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_inspection_rule_0 CHECK (result IN ('pass','fail','unknown'))\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE usage_event (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tinstallation_id UUID NOT NULL, \n\tobserved_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\trecorded_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\thours_increment NUMERIC(18, 6) NOT NULL, \n\tcycles_increment BIGINT NOT NULL, \n\treset_reason TEXT, \n\tCONSTRAINT pk_usage_event PRIMARY KEY (id), \n\tCONSTRAINT fk_usage_event_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_usage_event_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_usage_event_installation_id FOREIGN KEY(organization_id, installation_id) REFERENCES installation (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_usage_event_rule_0 CHECK (recorded_at >= observed_at), \n\tCONSTRAINT ck_usage_event_rule_1 CHECK (hours_increment >= 0 AND cycles_increment >= 0 AND (hours_increment > 0 OR cycles_increment > 0 OR reset_reason IS NOT NULL))\n)\n\n"
    )
    op.execute(
        "CREATE OR REPLACE FUNCTION fleetiq_immutable_evidence() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN\nRAISE EXCEPTION 'append-only evidence; append a superseding event'; END $$"
    )
    op.execute(
        "CREATE TRIGGER immutable_flight BEFORE UPDATE OR DELETE ON flight FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
    op.execute(
        "CREATE TRIGGER immutable_usage_event BEFORE UPDATE OR DELETE ON usage_event FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
    op.execute(
        "CREATE TRIGGER immutable_maintenance_event BEFORE UPDATE OR DELETE ON maintenance_event FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
    op.execute(
        "CREATE TRIGGER immutable_failure_event BEFORE UPDATE OR DELETE ON failure_event FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
    op.execute(
        "CREATE TRIGGER immutable_inspection BEFORE UPDATE OR DELETE ON inspection FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
    op.execute(
        "CREATE INDEX ix_failure_component_time ON failure_event(organization_id,component_id,observed_at)"
    )
    op.execute(
        "CREATE INDEX ix_maintenance_aircraft_time ON maintenance_event(organization_id,aircraft_id,occurred_at)"
    )
    op.execute(
        "GRANT SELECT ON failure_event,flight,maintenance_event,inspection,usage_event TO fleetiq_app, fleetiq_worker"
    )


def downgrade():
    op.execute("DROP TABLE usage_event")
    op.execute("DROP TABLE inspection")
    op.execute("DROP TABLE maintenance_event")
    op.execute("DROP TABLE flight")
    op.execute("DROP TABLE failure_event")
    op.execute("DROP FUNCTION fleetiq_immutable_evidence()")
