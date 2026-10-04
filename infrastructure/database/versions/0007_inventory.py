"""Frozen inventory schema."""

from alembic import op

revision = "0007_inventory"
down_revision = "0006_workflow"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "\nCREATE TABLE spare_part (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tcode TEXT NOT NULL, \n\tkind TEXT NOT NULL, \n\tpack_size NUMERIC(18, 6) DEFAULT 1 NOT NULL, \n\tserialized BOOLEAN DEFAULT false NOT NULL, \n\tCONSTRAINT pk_spare_part PRIMARY KEY (id), \n\tCONSTRAINT fk_spare_part_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_spare_part_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT ck_spare_part_rule_0 CHECK (pack_size > 0 AND pack_size < 'Infinity'::numeric), \n\tCONSTRAINT uq_spare_part_key_0 UNIQUE (organization_id, code)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE inventory (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tpart_id UUID NOT NULL, \n\tsite_id UUID NOT NULL, \n\tcondition TEXT NOT NULL, \n\ton_hand NUMERIC(18, 6) DEFAULT 0 NOT NULL, \n\treserved NUMERIC(18, 6) DEFAULT 0 NOT NULL, \n\tquarantined NUMERIC(18, 6) DEFAULT 0 NOT NULL, \n\tversion BIGINT DEFAULT 0 NOT NULL, \n\tCONSTRAINT pk_inventory PRIMARY KEY (id), \n\tCONSTRAINT fk_inventory_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_inventory_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_inventory_part_id FOREIGN KEY(organization_id, part_id) REFERENCES spare_part (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_inventory_site_id FOREIGN KEY(organization_id, site_id) REFERENCES site (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_inventory_rule_0 CHECK (condition IN ('serviceable','quarantined','repairable')), \n\tCONSTRAINT ck_inventory_rule_1 CHECK (on_hand >= 0 AND on_hand < 'Infinity'::numeric AND quarantined >= 0 AND quarantined <= on_hand), \n\tCONSTRAINT ck_inventory_rule_2 CHECK (reserved >= 0 AND reserved <= on_hand-quarantined AND version >= 0), \n\tCONSTRAINT ck_inventory_rule_3 CHECK (condition='serviceable' OR reserved=0), \n\tCONSTRAINT uq_inventory_key_0 UNIQUE (organization_id, part_id, site_id, condition)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE part_compatibility (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tpart_id UUID NOT NULL, \n\taircraft_type_id UUID NOT NULL, \n\tprocedure_revision_id UUID NOT NULL, \n\tCONSTRAINT pk_part_compatibility PRIMARY KEY (id), \n\tCONSTRAINT fk_part_compatibility_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_part_compatibility_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_part_compatibility_part_id FOREIGN KEY(organization_id, part_id) REFERENCES spare_part (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_part_compatibility_aircraft_type_id FOREIGN KEY(organization_id, aircraft_type_id) REFERENCES aircraft_type (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_part_compatibility_procedure_revision_id FOREIGN KEY(organization_id, procedure_revision_id) REFERENCES procedure_revision (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_part_compatibility_key_0 UNIQUE (organization_id, part_id, aircraft_type_id, procedure_revision_id)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE purchase_order (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tpart_id UUID NOT NULL, \n\tsite_id UUID NOT NULL, \n\tquantity NUMERIC(18, 6) NOT NULL, \n\texpected_arrival TIMESTAMP WITH TIME ZONE NOT NULL, \n\tconfirmed_arrival TIMESTAMP WITH TIME ZONE, \n\tstatus TEXT DEFAULT 'announced' NOT NULL, \n\tsupplier TEXT NOT NULL, \n\tlead_time_assumption JSONB NOT NULL, \n\tCONSTRAINT pk_purchase_order PRIMARY KEY (id), \n\tCONSTRAINT fk_purchase_order_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_purchase_order_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_purchase_order_part_id FOREIGN KEY(organization_id, part_id) REFERENCES spare_part (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_purchase_order_site_id FOREIGN KEY(organization_id, site_id) REFERENCES site (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_purchase_order_rule_0 CHECK (quantity > 0 AND quantity < 'Infinity'::numeric), \n\tCONSTRAINT ck_purchase_order_rule_1 CHECK (status IN ('announced','confirmed','received','cancelled')), \n\tCONSTRAINT ck_purchase_order_rule_2 CHECK (status <> 'received' OR confirmed_arrival IS NOT NULL)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE serialized_stock (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tcomponent_id UUID NOT NULL, \n\tinventory_id UUID NOT NULL, \n\tCONSTRAINT pk_serialized_stock PRIMARY KEY (id), \n\tCONSTRAINT fk_serialized_stock_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_serialized_stock_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_serialized_stock_component_id FOREIGN KEY(organization_id, component_id) REFERENCES component (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_serialized_stock_inventory_id FOREIGN KEY(organization_id, inventory_id) REFERENCES inventory (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_serialized_stock_key_0 UNIQUE (organization_id, component_id)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE part_reservation (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\ttask_id UUID NOT NULL, \n\tinventory_id UUID NOT NULL, \n\tplan_id UUID, \n\tquantity NUMERIC(18, 6) NOT NULL, \n\tstate TEXT DEFAULT 'reserved' NOT NULL, \n\tversion BIGINT DEFAULT 0 NOT NULL, \n\tCONSTRAINT pk_part_reservation PRIMARY KEY (id), \n\tCONSTRAINT fk_part_reservation_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_part_reservation_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_part_reservation_task_id FOREIGN KEY(organization_id, task_id) REFERENCES maintenance_task (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_part_reservation_inventory_id FOREIGN KEY(organization_id, inventory_id) REFERENCES inventory (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_part_reservation_rule_0 CHECK (quantity > 0 AND quantity < 'Infinity'::numeric), \n\tCONSTRAINT ck_part_reservation_rule_1 CHECK (state IN ('reserved','consumed','released')), \n\tCONSTRAINT ck_part_reservation_rule_2 CHECK (version >= 0)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE task_part (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\ttask_id UUID NOT NULL, \n\tpart_id UUID NOT NULL, \n\tquantity NUMERIC(18, 6) NOT NULL, \n\tCONSTRAINT pk_task_part PRIMARY KEY (id), \n\tCONSTRAINT fk_task_part_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_task_part_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_task_part_task_id FOREIGN KEY(organization_id, task_id) REFERENCES maintenance_task (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_task_part_part_id FOREIGN KEY(organization_id, part_id) REFERENCES spare_part (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_task_part_rule_0 CHECK (quantity > 0 AND quantity < 'Infinity'::numeric), \n\tCONSTRAINT uq_task_part_key_0 UNIQUE (organization_id, task_id, part_id)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE stock_movement (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tinventory_id UUID NOT NULL, \n\treservation_id UUID, \n\ttask_id UUID, \n\treverses_id UUID, \n\tquantity NUMERIC(18, 6) NOT NULL, \n\treason TEXT NOT NULL, \n\tidempotency_key TEXT NOT NULL, \n\tactor_id UUID NOT NULL, \n\toccurred_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_stock_movement PRIMARY KEY (id), \n\tCONSTRAINT fk_stock_movement_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_stock_movement_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_stock_movement_inventory_id FOREIGN KEY(organization_id, inventory_id) REFERENCES inventory (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_stock_movement_reservation_id FOREIGN KEY(organization_id, reservation_id) REFERENCES part_reservation (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_stock_movement_task_id FOREIGN KEY(organization_id, task_id) REFERENCES maintenance_task (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_stock_movement_reverses_id FOREIGN KEY(organization_id, reverses_id) REFERENCES stock_movement (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_stock_movement_rule_0 CHECK (quantity <> 0 AND quantity > '-Infinity'::numeric AND quantity < 'Infinity'::numeric), \n\tCONSTRAINT ck_stock_movement_rule_1 CHECK (reverses_id IS NULL OR reverses_id <> id), \n\tCONSTRAINT uq_stock_movement_key_0 UNIQUE (organization_id, idempotency_key), \n\tCONSTRAINT uq_stock_movement_key_1 UNIQUE (organization_id, reverses_id)\n)\n\n"
    )
    op.execute(
        "ALTER TABLE component ADD CONSTRAINT fk_component_part_id FOREIGN KEY(organization_id, part_id) REFERENCES spare_part (organization_id, id) ON DELETE RESTRICT"
    )
    op.execute("ALTER TABLE component ALTER COLUMN part_id SET NOT NULL")
    op.execute(
        "CREATE UNIQUE INDEX uq_active_task_stock_reservation ON part_reservation (organization_id, task_id, inventory_id) WHERE state='reserved'"
    )
    op.execute(
        "CREATE TRIGGER immutable_stock_movement BEFORE UPDATE OR DELETE ON stock_movement FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
    op.execute(
        "CREATE OR REPLACE FUNCTION fleetiq_validate_task_part() RETURNS trigger LANGUAGE plpgsql AS $$\nBEGIN IF NOT EXISTS(SELECT 1 FROM maintenance_task t JOIN work_order w ON\nw.id=t.work_order_id AND w.organization_id=t.organization_id JOIN aircraft a ON\na.id=w.aircraft_id AND a.organization_id=w.organization_id JOIN part_compatibility p ON\np.aircraft_type_id=a.type_id AND p.procedure_revision_id=t.procedure_revision_id\nAND p.organization_id=t.organization_id WHERE t.id=NEW.task_id\nAND t.organization_id=NEW.organization_id AND p.part_id=NEW.part_id) THEN\nRAISE EXCEPTION 'part incompatible with task aircraft and procedure'; END IF; RETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER validate_task_part BEFORE INSERT OR UPDATE ON task_part FOR EACH ROW EXECUTE FUNCTION fleetiq_validate_task_part()"
    )
    op.execute(
        "CREATE OR REPLACE FUNCTION fleetiq_validate_serialized_stock() RETURNS trigger LANGUAGE plpgsql AS $$\nBEGIN IF NOT EXISTS(SELECT 1 FROM inventory i JOIN component c ON c.part_id=i.part_id\nAND c.organization_id=i.organization_id JOIN spare_part p ON p.id=i.part_id\nAND p.organization_id=i.organization_id WHERE i.id=NEW.inventory_id AND c.id=NEW.component_id\nAND i.organization_id=NEW.organization_id AND p.serialized AND NOT EXISTS\n(SELECT 1 FROM installation x WHERE x.organization_id=c.organization_id\nAND x.component_id=c.id AND x.removed_at IS NULL)) THEN\nRAISE EXCEPTION 'serialized stock requires matching uninstalled serialized component'; END IF;\nRETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER validate_serialized_stock BEFORE INSERT OR UPDATE ON serialized_stock FOR EACH ROW EXECUTE FUNCTION fleetiq_validate_serialized_stock()"
    )
    op.execute(
        "CREATE OR REPLACE FUNCTION fleetiq_validate_reversal() RETURNS trigger LANGUAGE plpgsql AS $$\nBEGIN IF NEW.reverses_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM stock_movement m\nWHERE m.id=NEW.reverses_id AND m.organization_id=NEW.organization_id\nAND m.inventory_id=NEW.inventory_id AND m.quantity=-NEW.quantity AND m.reverses_id IS NULL)\nTHEN RAISE EXCEPTION 'reversal must negate original movement in the same stock row'; END IF;\nRETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER validate_reversal BEFORE INSERT ON stock_movement FOR EACH ROW EXECUTE FUNCTION fleetiq_validate_reversal()"
    )
    op.execute(
        "GRANT SELECT ON spare_part,inventory,part_compatibility,purchase_order,serialized_stock,part_reservation,task_part,stock_movement TO fleetiq_app, fleetiq_worker"
    )


def downgrade():
    op.execute("ALTER TABLE component DROP CONSTRAINT fk_component_part_id")
    op.execute("ALTER TABLE component ALTER COLUMN part_id DROP NOT NULL")
    op.execute("DROP TRIGGER validate_task_part ON task_part")
    op.execute("DROP FUNCTION fleetiq_validate_task_part()")
    op.execute("DROP TRIGGER validate_serialized_stock ON serialized_stock")
    op.execute("DROP FUNCTION fleetiq_validate_serialized_stock()")
    op.execute("DROP TRIGGER validate_reversal ON stock_movement")
    op.execute("DROP FUNCTION fleetiq_validate_reversal()")
    op.execute("DROP TABLE stock_movement")
    op.execute("DROP TABLE task_part")
    op.execute("DROP TABLE part_reservation")
    op.execute("DROP TABLE serialized_stock")
    op.execute("DROP TABLE purchase_order")
    op.execute("DROP TABLE part_compatibility")
    op.execute("DROP TABLE inventory")
    op.execute("DROP TABLE spare_part")
