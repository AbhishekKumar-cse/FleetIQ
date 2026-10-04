"""Frozen components schema."""

from alembic import op

revision = "0003_components"
down_revision = "0002_assets"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "\nCREATE TABLE component (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tserial TEXT NOT NULL, \n\tpart_id UUID, \n\tkind TEXT NOT NULL, \n\tmanufacture_date DATE, \n\tCONSTRAINT pk_component PRIMARY KEY (id), \n\tCONSTRAINT fk_component_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_component_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT uq_component_key_0 UNIQUE (organization_id, serial)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE engine (\n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tcomponent_id UUID NOT NULL, \n\tengine_type TEXT NOT NULL, \n\tCONSTRAINT pk_engine PRIMARY KEY (component_id), \n\tCONSTRAINT fk_engine_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_engine_component_id FOREIGN KEY(organization_id, component_id) REFERENCES component (organization_id, id) ON DELETE RESTRICT\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE installation (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\taircraft_id UUID NOT NULL, \n\tcomponent_id UUID NOT NULL, \n\tposition TEXT NOT NULL, \n\tinstalled_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tremoved_at TIMESTAMP WITH TIME ZONE, \n\tinitial_hours NUMERIC(18, 6) DEFAULT 0 NOT NULL, \n\tinitial_cycles BIGINT DEFAULT 0 NOT NULL, \n\tCONSTRAINT pk_installation PRIMARY KEY (id), \n\tCONSTRAINT fk_installation_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_installation_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_installation_aircraft_id FOREIGN KEY(organization_id, aircraft_id) REFERENCES aircraft (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_installation_component_id FOREIGN KEY(organization_id, component_id) REFERENCES component (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_installation_rule_0 CHECK (removed_at IS NULL OR removed_at > installed_at), \n\tCONSTRAINT ck_installation_rule_1 CHECK (initial_hours >= 0 AND initial_cycles >= 0)\n)\n\n"
    )
    op.execute(
        "ALTER TABLE installation ADD CONSTRAINT installation_no_component_overlap EXCLUDE USING gist (organization_id WITH =, component_id WITH =, tstzrange(installed_at, removed_at, '[)') WITH &&)"
    )
    op.execute(
        "ALTER TABLE installation ADD CONSTRAINT installation_no_position_overlap EXCLUDE USING gist (organization_id WITH =, aircraft_id WITH =, position WITH =, tstzrange(installed_at, removed_at, '[)') WITH &&)"
    )
    op.execute("GRANT SELECT ON component,engine,installation TO fleetiq_app, fleetiq_worker")


def downgrade():
    op.execute("DROP TABLE installation")
    op.execute("DROP TABLE engine")
    op.execute("DROP TABLE component")
