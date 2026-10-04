"""Frozen work schema."""

from alembic import op

revision = "0006_workflow"
down_revision = "0005_technical_history"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "\nCREATE TABLE procedure_revision (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tcode TEXT NOT NULL, \n\trevision TEXT NOT NULL, \n\tauthority_label TEXT NOT NULL, \n\tduration_slots BIGINT NOT NULL, \n\tskills JSONB NOT NULL, \n\tpart_requirements JSONB NOT NULL, \n\tCONSTRAINT pk_procedure_revision PRIMARY KEY (id), \n\tCONSTRAINT fk_procedure_revision_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_procedure_revision_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT ck_procedure_revision_rule_0 CHECK (duration_slots > 0), \n\tCONSTRAINT uq_procedure_revision_key_0 UNIQUE (organization_id, code, revision)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE recommendation (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\taircraft_id UUID NOT NULL, \n\tcomponent_id UUID NOT NULL, \n\tpolicy_version TEXT NOT NULL, \n\turgency TEXT NOT NULL, \n\trationale JSONB NOT NULL, \n\tstate TEXT DEFAULT 'new' NOT NULL, \n\tversion BIGINT DEFAULT 0 NOT NULL, \n\tCONSTRAINT pk_recommendation PRIMARY KEY (id), \n\tCONSTRAINT fk_recommendation_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_recommendation_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_recommendation_aircraft_id FOREIGN KEY(organization_id, aircraft_id) REFERENCES aircraft (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_recommendation_component_id FOREIGN KEY(organization_id, component_id) REFERENCES component (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_recommendation_rule_0 CHECK (state IN ('new','engineering_review','accepted','rejected')), \n\tCONSTRAINT ck_recommendation_rule_1 CHECK (version >= 0)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE recommendation_evidence (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\trecommendation_id UUID NOT NULL, \n\tprediction_id UUID NOT NULL, \n\tCONSTRAINT pk_recommendation_evidence PRIMARY KEY (id), \n\tCONSTRAINT fk_recommendation_evidence_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_recommendation_evidence_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_recommendation_evidence_recommendation_id FOREIGN KEY(organization_id, recommendation_id) REFERENCES recommendation (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_recommendation_evidence_key_0 UNIQUE (organization_id, recommendation_id, prediction_id)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE work_order (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\taircraft_id UUID NOT NULL, \n\tcomponent_id UUID NOT NULL, \n\trecommendation_id UUID NOT NULL, \n\tstate TEXT DEFAULT 'new' NOT NULL, \n\tversion BIGINT DEFAULT 0 NOT NULL, \n\ttechnical_scope JSONB, \n\tplan_approval JSONB, \n\tCONSTRAINT pk_work_order PRIMARY KEY (id), \n\tCONSTRAINT fk_work_order_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_work_order_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_work_order_aircraft_id FOREIGN KEY(organization_id, aircraft_id) REFERENCES aircraft (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_work_order_component_id FOREIGN KEY(organization_id, component_id) REFERENCES component (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_work_order_recommendation_id FOREIGN KEY(organization_id, recommendation_id) REFERENCES recommendation (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_work_order_rule_0 CHECK (state IN ('new','engineering_review','accepted','rejected','planner_draft','schedule_proposed','schedule_approved','executing','inspection_pending','released','held','closed')), \n\tCONSTRAINT ck_work_order_rule_1 CHECK (version >= 0)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE approval (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\twork_order_id UUID NOT NULL, \n\tactor_id UUID NOT NULL, \n\taction TEXT NOT NULL, \n\treason TEXT NOT NULL, \n\ttarget_version BIGINT NOT NULL, \n\toccurred_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_approval PRIMARY KEY (id), \n\tCONSTRAINT fk_approval_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_approval_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_approval_work_order_id FOREIGN KEY(organization_id, work_order_id) REFERENCES work_order (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_approval_rule_0 CHECK (target_version >= 0), \n\tCONSTRAINT ck_approval_rule_1 CHECK (length(trim(reason)) > 0)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE maintenance_task (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\twork_order_id UUID NOT NULL, \n\tprocedure_revision_id UUID NOT NULL, \n\tduration_slots BIGINT NOT NULL, \n\tdeadline TIMESTAMP WITH TIME ZONE, \n\tstate TEXT DEFAULT 'pending' NOT NULL, \n\tversion BIGINT DEFAULT 0 NOT NULL, \n\tCONSTRAINT pk_maintenance_task PRIMARY KEY (id), \n\tCONSTRAINT fk_maintenance_task_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_maintenance_task_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_maintenance_task_work_order_id FOREIGN KEY(organization_id, work_order_id) REFERENCES work_order (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_maintenance_task_procedure_revision_id FOREIGN KEY(organization_id, procedure_revision_id) REFERENCES procedure_revision (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_maintenance_task_rule_0 CHECK (duration_slots > 0 AND version >= 0), \n\tCONSTRAINT ck_maintenance_task_rule_1 CHECK (state IN ('pending','executing','completed','held'))\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE task_dependency (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tpredecessor_id UUID NOT NULL, \n\tsuccessor_id UUID NOT NULL, \n\tCONSTRAINT pk_task_dependency PRIMARY KEY (id), \n\tCONSTRAINT fk_task_dependency_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_task_dependency_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_task_dependency_predecessor_id FOREIGN KEY(organization_id, predecessor_id) REFERENCES maintenance_task (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_task_dependency_successor_id FOREIGN KEY(organization_id, successor_id) REFERENCES maintenance_task (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_task_dependency_rule_0 CHECK (predecessor_id <> successor_id), \n\tCONSTRAINT uq_task_dependency_key_0 UNIQUE (organization_id, predecessor_id, successor_id)\n)\n\n"
    )
    op.execute(
        "ALTER TABLE maintenance_event ADD CONSTRAINT fk_maintenance_event_task_id FOREIGN KEY(organization_id, task_id) REFERENCES maintenance_task (organization_id, id) ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE inspection ADD CONSTRAINT fk_inspection_task_id FOREIGN KEY(organization_id, task_id) REFERENCES maintenance_task (organization_id, id) ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE inspection ADD CONSTRAINT fk_inspection_procedure_revision_id FOREIGN KEY(organization_id, procedure_revision_id) REFERENCES procedure_revision (organization_id, id) ON DELETE RESTRICT"
    )
    op.execute(
        "CREATE TRIGGER immutable_approval BEFORE UPDATE OR DELETE ON approval FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
    op.execute(
        "CREATE TRIGGER immutable_procedure_revision BEFORE UPDATE OR DELETE ON procedure_revision FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
    op.execute(
        "CREATE OR REPLACE FUNCTION fleetiq_validate_inspection() RETURNS trigger LANGUAGE plpgsql AS $$\nBEGIN\nIF NOT EXISTS (SELECT 1 FROM maintenance_task t JOIN work_order w ON\nw.organization_id=t.organization_id AND w.id=t.work_order_id\nJOIN maintenance_event e ON e.organization_id=w.organization_id AND e.id=NEW.maintenance_event_id\nWHERE t.organization_id=NEW.organization_id AND t.id=NEW.task_id AND t.state='completed'\nAND t.procedure_revision_id=NEW.procedure_revision_id AND e.task_id=t.id\nAND e.aircraft_id=w.aircraft_id AND e.component_id=w.component_id\nAND NEW.completed_at>=e.occurred_at) THEN\nRAISE EXCEPTION 'inspection requires completed matching task, procedure and evidence'; END IF;\nRETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER validate_inspection BEFORE INSERT ON inspection FOR EACH ROW EXECUTE FUNCTION fleetiq_validate_inspection()"
    )
    op.execute(
        "GRANT SELECT ON procedure_revision,recommendation,recommendation_evidence,work_order,approval,maintenance_task,task_dependency TO fleetiq_app, fleetiq_worker"
    )


def downgrade():
    op.execute("DROP TRIGGER validate_inspection ON inspection")
    op.execute("DROP FUNCTION fleetiq_validate_inspection()")
    op.execute("ALTER TABLE inspection DROP CONSTRAINT fk_inspection_procedure_revision_id")
    op.execute("ALTER TABLE inspection DROP CONSTRAINT fk_inspection_task_id")
    op.execute("ALTER TABLE maintenance_event DROP CONSTRAINT fk_maintenance_event_task_id")
    op.execute("DROP TABLE task_dependency")
    op.execute("DROP TABLE maintenance_task")
    op.execute("DROP TABLE approval")
    op.execute("DROP TABLE work_order")
    op.execute("DROP TABLE recommendation_evidence")
    op.execute("DROP TABLE recommendation")
    op.execute("DROP TABLE procedure_revision")
