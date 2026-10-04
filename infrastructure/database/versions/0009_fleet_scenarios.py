"""Frozen fleet schema."""

from alembic import op

revision = "0009_fleet_scenarios"
down_revision = "0008_predictions"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "\nCREATE TABLE fleet_snapshot (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tfleet_id UUID NOT NULL, \n\tas_of TIMESTAMP WITH TIME ZONE NOT NULL, \n\tsource_cutoff TIMESTAMP WITH TIME ZONE NOT NULL, \n\tscope TEXT NOT NULL, \n\tscenario_id UUID, \n\tcounts JSONB NOT NULL, \n\ttotal BIGINT NOT NULL, \n\tserviceable BIGINT NOT NULL, \n\tmaintenance BIGINT NOT NULL, \n\tgrounded_other BIGINT NOT NULL, \n\tunknown BIGINT NOT NULL, \n\tinventory_state JSONB NOT NULL, \n\tconfiguration_hash TEXT NOT NULL, \n\tversions JSONB NOT NULL, \n\tCONSTRAINT pk_fleet_snapshot PRIMARY KEY (id), \n\tCONSTRAINT fk_fleet_snapshot_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_fleet_snapshot_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_fleet_snapshot_fleet_id FOREIGN KEY(organization_id, fleet_id) REFERENCES fleet (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_fleet_snapshot_rule_0 CHECK (scope IN ('recorded','projected')), \n\tCONSTRAINT ck_fleet_snapshot_rule_1 CHECK ((scope='projected')=(scenario_id IS NOT NULL)), \n\tCONSTRAINT ck_fleet_snapshot_rule_2 CHECK (source_cutoff<=as_of), \n\tCONSTRAINT ck_fleet_snapshot_rule_3 CHECK (total>=0 AND serviceable>=0 AND maintenance>=0 AND grounded_other>=0 AND unknown>=0), \n\tCONSTRAINT ck_fleet_snapshot_rule_4 CHECK (total=serviceable+maintenance+grounded_other+unknown), \n\tCONSTRAINT ck_fleet_snapshot_rule_5 CHECK (configuration_hash ~ '^[0-9a-f]{64}$'), \n\tCONSTRAINT ck_fleet_snapshot_rule_6 CHECK (counts ?& ARRAY['serviceable','maintenance','grounded_other','unknown'] AND (counts->>'serviceable')::bigint=serviceable AND (counts->>'maintenance')::bigint=maintenance AND (counts->>'grounded_other')::bigint=grounded_other AND (counts->>'unknown')::bigint=unknown)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE resource_calendar (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tsite_id UUID NOT NULL, \n\tslot_start TIMESTAMP WITH TIME ZONE NOT NULL, \n\tslot_end TIMESTAMP WITH TIME ZONE NOT NULL, \n\tbay_capacity BIGINT NOT NULL, \n\tskill_pools JSONB NOT NULL, \n\tversion BIGINT NOT NULL, \n\tCONSTRAINT pk_resource_calendar PRIMARY KEY (id), \n\tCONSTRAINT fk_resource_calendar_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_resource_calendar_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_resource_calendar_site_id FOREIGN KEY(organization_id, site_id) REFERENCES site (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_resource_calendar_rule_0 CHECK (slot_end>slot_start), \n\tCONSTRAINT ck_resource_calendar_rule_1 CHECK (bay_capacity>=0 AND version>=0), \n\tCONSTRAINT ck_resource_calendar_rule_2 CHECK (jsonb_typeof(skill_pools)='object'), \n\tCONSTRAINT uq_resource_calendar_key_0 UNIQUE (organization_id, site_id, slot_start, version)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE aircraft_snapshot (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tfleet_snapshot_id UUID NOT NULL, \n\taircraft_id UUID NOT NULL, \n\tstatus TEXT NOT NULL, \n\tsource_cutoff TIMESTAMP WITH TIME ZONE NOT NULL, \n\tevidence JSONB NOT NULL, \n\tat_risk BOOLEAN DEFAULT false NOT NULL, \n\tCONSTRAINT pk_aircraft_snapshot PRIMARY KEY (id), \n\tCONSTRAINT fk_aircraft_snapshot_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_aircraft_snapshot_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_aircraft_snapshot_fleet_snapshot_id FOREIGN KEY(organization_id, fleet_snapshot_id) REFERENCES fleet_snapshot (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_aircraft_snapshot_aircraft_id FOREIGN KEY(organization_id, aircraft_id) REFERENCES aircraft (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_aircraft_snapshot_rule_0 CHECK (status IN ('serviceable','maintenance','grounded_other','unknown')), \n\tCONSTRAINT uq_aircraft_snapshot_key_0 UNIQUE (organization_id, fleet_snapshot_id, aircraft_id)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE aircraft_status_event (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\taircraft_id UUID NOT NULL, \n\tcomponent_id UUID, \n\toccurred_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\trecorded_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tstatus TEXT NOT NULL, \n\treason TEXT NOT NULL, \n\tactor_id UUID NOT NULL, \n\tversion BIGINT NOT NULL, \n\torigin TEXT DEFAULT 'recorded' NOT NULL, \n\tCONSTRAINT pk_aircraft_status_event PRIMARY KEY (id), \n\tCONSTRAINT fk_aircraft_status_event_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_aircraft_status_event_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_aircraft_status_event_aircraft_id FOREIGN KEY(organization_id, aircraft_id) REFERENCES aircraft (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_aircraft_status_event_component_id FOREIGN KEY(organization_id, component_id) REFERENCES component (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_aircraft_status_event_rule_0 CHECK (status IN ('serviceable','maintenance','grounded_other','unknown')), \n\tCONSTRAINT ck_aircraft_status_event_rule_1 CHECK (recorded_at>=occurred_at), \n\tCONSTRAINT ck_aircraft_status_event_rule_2 CHECK (version>=0), \n\tCONSTRAINT ck_aircraft_status_event_rule_3 CHECK (origin='recorded'), \n\tCONSTRAINT uq_aircraft_status_event_key_0 UNIQUE (organization_id, aircraft_id, version)\n)\n\n"
    )
    op.execute(
        '\nCREATE TABLE downtime_interval (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\taircraft_id UUID NOT NULL, \n\tcomponent_id UUID, \n\tstart TIMESTAMP WITH TIME ZONE NOT NULL, \n\t"end" TIMESTAMP WITH TIME ZONE, \n\treason TEXT NOT NULL, \n\tprimary_reason_rank BIGINT NOT NULL, \n\trecorded_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_downtime_interval PRIMARY KEY (id), \n\tCONSTRAINT fk_downtime_interval_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_downtime_interval_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_downtime_interval_aircraft_id FOREIGN KEY(organization_id, aircraft_id) REFERENCES aircraft (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_downtime_interval_component_id FOREIGN KEY(organization_id, component_id) REFERENCES component (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_downtime_interval_rule_0 CHECK ("end" IS NULL OR "end">start), \n\tCONSTRAINT ck_downtime_interval_rule_1 CHECK (recorded_at>=start), \n\tCONSTRAINT ck_downtime_interval_rule_2 CHECK (primary_reason_rank>=0)\n)\n\n'
    )
    op.execute(
        "\nCREATE TABLE scenario (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tsnapshot_id UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\tassumptions JSONB NOT NULL, \n\tchanges JSONB NOT NULL, \n\tconfiguration_hash TEXT NOT NULL, \n\tunit_mapping JSONB NOT NULL, \n\thorizon_slots BIGINT NOT NULL, \n\treplicates BIGINT NOT NULL, \n\tCONSTRAINT pk_scenario PRIMARY KEY (id), \n\tCONSTRAINT fk_scenario_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_scenario_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_scenario_snapshot_id FOREIGN KEY(organization_id, snapshot_id) REFERENCES fleet_snapshot (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_scenario_rule_0 CHECK (horizon_slots BETWEEN 1 AND 8760), \n\tCONSTRAINT ck_scenario_rule_1 CHECK (replicates BETWEEN 1 AND 1000), \n\tCONSTRAINT ck_scenario_rule_2 CHECK (configuration_hash ~ '^[0-9a-f]{64}$')\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE schedule_plan (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tsnapshot_id UUID NOT NULL, \n\tpolicy_version_id UUID NOT NULL, \n\tmodel_hash TEXT NOT NULL, \n\tpolicy_hash TEXT NOT NULL, \n\tsolver_status TEXT NOT NULL, \n\tsolver_gap DOUBLE PRECISION, \n\tstate TEXT DEFAULT 'draft' NOT NULL, \n\tversion BIGINT DEFAULT 0 NOT NULL, \n\tchecker_passed BOOLEAN DEFAULT false NOT NULL, \n\treviewed_version BIGINT, \n\tapproved_by UUID, \n\tCONSTRAINT pk_schedule_plan PRIMARY KEY (id), \n\tCONSTRAINT fk_schedule_plan_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_schedule_plan_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_schedule_plan_snapshot_id FOREIGN KEY(organization_id, snapshot_id) REFERENCES fleet_snapshot (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_schedule_plan_policy_version_id FOREIGN KEY(organization_id, policy_version_id) REFERENCES policy_version (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_schedule_plan_rule_0 CHECK (state IN ('draft','proposed','approved','rejected')), \n\tCONSTRAINT ck_schedule_plan_rule_1 CHECK (version>=0), \n\tCONSTRAINT ck_schedule_plan_rule_2 CHECK (solver_gap IS NULL OR (solver_gap>=0 AND solver_gap<=1)), \n\tCONSTRAINT ck_schedule_plan_rule_3 CHECK (model_hash ~ '^[0-9a-f]{64}$' AND policy_hash ~ '^[0-9a-f]{64}$'), \n\tCONSTRAINT ck_schedule_plan_rule_4 CHECK (state<>'approved' OR (checker_passed AND reviewed_version IS NOT NULL AND reviewed_version=version AND approved_by IS NOT NULL))\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE scenario_run (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tscenario_id UUID NOT NULL, \n\tseed BIGINT NOT NULL, \n\treplicate BIGINT NOT NULL, \n\tstate TEXT DEFAULT 'pending' NOT NULL, \n\toutcome_metrics JSONB, \n\terror TEXT, \n\tCONSTRAINT pk_scenario_run PRIMARY KEY (id), \n\tCONSTRAINT fk_scenario_run_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_scenario_run_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_scenario_run_scenario_id FOREIGN KEY(organization_id, scenario_id) REFERENCES scenario (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_scenario_run_rule_0 CHECK (seed>=0 AND replicate>=0), \n\tCONSTRAINT ck_scenario_run_rule_1 CHECK (state IN ('pending','running','completed','failed')), \n\tCONSTRAINT ck_scenario_run_rule_2 CHECK (state<>'completed' OR outcome_metrics IS NOT NULL), \n\tCONSTRAINT ck_scenario_run_rule_3 CHECK (state<>'failed' OR error IS NOT NULL), \n\tCONSTRAINT uq_scenario_run_key_0 UNIQUE (organization_id, scenario_id, seed, replicate)\n)\n\n"
    )
    op.execute(
        '\nCREATE TABLE schedule_assignment (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tplan_id UUID NOT NULL, \n\ttask_id UUID NOT NULL, \n\tsite_id UUID NOT NULL, \n\tstart TIMESTAMP WITH TIME ZONE NOT NULL, \n\t"end" TIMESTAMP WITH TIME ZONE NOT NULL, \n\tresource_commitments JSONB NOT NULL, \n\tCONSTRAINT pk_schedule_assignment PRIMARY KEY (id), \n\tCONSTRAINT fk_schedule_assignment_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_schedule_assignment_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_schedule_assignment_plan_id FOREIGN KEY(organization_id, plan_id) REFERENCES schedule_plan (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_schedule_assignment_task_id FOREIGN KEY(organization_id, task_id) REFERENCES maintenance_task (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_schedule_assignment_site_id FOREIGN KEY(organization_id, site_id) REFERENCES site (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_schedule_assignment_rule_0 CHECK ("end">start), \n\tCONSTRAINT uq_schedule_assignment_key_0 UNIQUE (organization_id, plan_id, task_id)\n)\n\n'
    )
    op.execute(
        "CREATE INDEX ix_fleet_scope_time ON fleet_snapshot (organization_id, fleet_id, scope, as_of)"
    )
    op.execute(
        "CREATE INDEX ix_status_aircraft_time ON aircraft_status_event (organization_id, aircraft_id, occurred_at)"
    )
    op.execute(
        "CREATE INDEX ix_downtime_aircraft_time ON downtime_interval (organization_id, aircraft_id, start)"
    )
    op.execute(
        "ALTER TABLE fleet_snapshot ADD CONSTRAINT fk_fleet_snapshot_scenario_id FOREIGN KEY(organization_id, scenario_id) REFERENCES scenario (organization_id, id) ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE part_reservation ADD CONSTRAINT fk_part_reservation_plan_id FOREIGN KEY(organization_id, plan_id) REFERENCES schedule_plan (organization_id, id) ON DELETE RESTRICT"
    )
    op.execute(
        "CREATE TRIGGER immutable_aircraft_status_event BEFORE UPDATE OR DELETE ON aircraft_status_event FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
    op.execute(
        "CREATE TRIGGER immutable_downtime_interval BEFORE UPDATE OR DELETE ON downtime_interval FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
    op.execute(
        "CREATE TRIGGER immutable_fleet_snapshot BEFORE UPDATE OR DELETE ON fleet_snapshot FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
    op.execute(
        "CREATE TRIGGER immutable_aircraft_snapshot BEFORE UPDATE OR DELETE ON aircraft_snapshot FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
    op.execute(
        "CREATE TRIGGER immutable_scenario BEFORE UPDATE OR DELETE ON scenario FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
    op.execute(
        "CREATE FUNCTION fleetiq_validate_scenario_scope() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN\nIF TG_TABLE_NAME='scenario' THEN\nIF NOT EXISTS(SELECT 1 FROM fleet_snapshot f WHERE f.organization_id=NEW.organization_id\nAND f.id=NEW.snapshot_id AND f.scope='recorded') THEN RAISE EXCEPTION 'scenario needs recorded baseline'; END IF;\nELSIF TG_TABLE_NAME='fleet_snapshot' THEN\nIF current_user='fleetiq_worker' AND NEW.scope<>'projected' THEN RAISE EXCEPTION 'simulation worker cannot record live snapshots'; END IF;\nIF NEW.scenario_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM scenario s JOIN fleet_snapshot f\nON f.id=s.snapshot_id AND f.organization_id=s.organization_id WHERE s.id=NEW.scenario_id\nAND s.organization_id=NEW.organization_id AND f.fleet_id=NEW.fleet_id AND f.source_cutoff=NEW.source_cutoff)\nTHEN RAISE EXCEPTION 'projection must retain scoped baseline cutoff'; END IF;\nELSIF TG_TABLE_NAME='aircraft_snapshot' THEN\nIF NOT EXISTS(SELECT 1 FROM fleet_snapshot f JOIN aircraft a ON a.fleet_id=f.fleet_id\nAND a.organization_id=f.organization_id WHERE f.id=NEW.fleet_snapshot_id AND f.organization_id=NEW.organization_id\nAND a.id=NEW.aircraft_id AND f.source_cutoff=NEW.source_cutoff AND\n(current_user<>'fleetiq_worker' OR f.scope='projected')) THEN RAISE EXCEPTION 'aircraft snapshot scope/cutoff mismatch'; END IF;\nELSIF TG_TABLE_NAME='scenario_run' THEN\nIF NOT EXISTS(SELECT 1 FROM scenario s WHERE s.id=NEW.scenario_id AND s.organization_id=NEW.organization_id\nAND NEW.replicate<s.replicates) THEN RAISE EXCEPTION 'replicate outside scenario'; END IF;\nEND IF; RETURN NEW; END $$"
    )
    op.execute(
        "CREATE FUNCTION fleetiq_validate_skill_pools() RETURNS trigger LANGUAGE plpgsql AS $$\nDECLARE value jsonb; n bigint; distinct_n bigint; BEGIN\nFOR value IN SELECT v FROM jsonb_each(NEW.skill_pools) e(k,v) LOOP\nIF jsonb_typeof(value)<>'array' THEN RAISE EXCEPTION 'skill pools require worker arrays'; END IF; END LOOP;\nSELECT count(*),count(DISTINCT worker) INTO n,distinct_n FROM jsonb_each(NEW.skill_pools) e(k,v),\njsonb_array_elements_text(v) a(worker); IF n<>distinct_n THEN RAISE EXCEPTION 'worker counted in multiple pools'; END IF;\nRETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER validate_skill_pools BEFORE INSERT OR UPDATE ON resource_calendar FOR EACH ROW EXECUTE FUNCTION fleetiq_validate_skill_pools()"
    )
    op.execute("GRANT INSERT ON scenario_run,fleet_snapshot,aircraft_snapshot TO fleetiq_worker")
    op.execute("GRANT UPDATE (state,outcome_metrics,error) ON scenario_run TO fleetiq_worker")
    op.execute(
        "CREATE TRIGGER validate_scope_scenario BEFORE INSERT OR UPDATE ON scenario FOR EACH ROW EXECUTE FUNCTION fleetiq_validate_scenario_scope()"
    )
    op.execute(
        "CREATE TRIGGER validate_scope_fleet_snapshot BEFORE INSERT OR UPDATE ON fleet_snapshot FOR EACH ROW EXECUTE FUNCTION fleetiq_validate_scenario_scope()"
    )
    op.execute(
        "CREATE TRIGGER validate_scope_aircraft_snapshot BEFORE INSERT OR UPDATE ON aircraft_snapshot FOR EACH ROW EXECUTE FUNCTION fleetiq_validate_scenario_scope()"
    )
    op.execute(
        "CREATE TRIGGER validate_scope_scenario_run BEFORE INSERT OR UPDATE ON scenario_run FOR EACH ROW EXECUTE FUNCTION fleetiq_validate_scenario_scope()"
    )
    op.execute(
        "GRANT SELECT ON fleet_snapshot,resource_calendar,aircraft_snapshot,aircraft_status_event,downtime_interval,scenario,schedule_plan,scenario_run,schedule_assignment TO fleetiq_app, fleetiq_worker"
    )


def downgrade():
    op.execute("ALTER TABLE fleet_snapshot DROP CONSTRAINT fk_fleet_snapshot_scenario_id")
    op.execute("ALTER TABLE part_reservation DROP CONSTRAINT fk_part_reservation_plan_id")
    op.execute("DROP TRIGGER validate_scope_scenario ON scenario")
    op.execute("DROP TRIGGER validate_scope_fleet_snapshot ON fleet_snapshot")
    op.execute("DROP TRIGGER validate_scope_aircraft_snapshot ON aircraft_snapshot")
    op.execute("DROP TRIGGER validate_scope_scenario_run ON scenario_run")
    op.execute("DROP FUNCTION fleetiq_validate_scenario_scope()")
    op.execute("DROP TRIGGER validate_skill_pools ON resource_calendar")
    op.execute("DROP FUNCTION fleetiq_validate_skill_pools()")
    op.execute("DROP TABLE schedule_assignment")
    op.execute("DROP TABLE scenario_run")
    op.execute("DROP TABLE schedule_plan")
    op.execute("DROP TABLE scenario")
    op.execute("DROP TABLE downtime_interval")
    op.execute("DROP TABLE aircraft_status_event")
    op.execute("DROP TABLE aircraft_snapshot")
    op.execute("DROP TABLE resource_calendar")
    op.execute("DROP TABLE fleet_snapshot")
