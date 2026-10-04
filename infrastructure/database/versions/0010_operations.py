"""Frozen operations schema."""

from alembic import op

revision = "0010_operations"
down_revision = "0009_fleet_scenarios"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "\nCREATE TABLE auth_rate_limit (\n\tkey_hash TEXT NOT NULL, \n\twindow_start TIMESTAMP WITH TIME ZONE NOT NULL, \n\tattempts BIGINT NOT NULL, \n\tCONSTRAINT pk_auth_rate_limit PRIMARY KEY (key_hash), \n\tCONSTRAINT ck_auth_rate_limit_attempts CHECK (attempts>=0), \n\tCONSTRAINT ck_auth_rate_limit_hash CHECK (key_hash ~ '^[0-9a-f]{64}$')\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE app_user (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tsubject TEXT NOT NULL, \n\tdisplay_name TEXT NOT NULL, \n\tpassword_hash TEXT NOT NULL, \n\tactive BOOLEAN DEFAULT true NOT NULL, \n\tpassword_version BIGINT DEFAULT 0 NOT NULL, \n\tCONSTRAINT pk_app_user PRIMARY KEY (id), \n\tCONSTRAINT fk_app_user_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_app_user_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT ck_app_user_rule_0 CHECK (subject=lower(subject) AND length(trim(subject))>0), \n\tCONSTRAINT ck_app_user_rule_1 CHECK (password_hash LIKE '$argon2id$%%'), \n\tCONSTRAINT ck_app_user_rule_2 CHECK (password_version>=0), \n\tCONSTRAINT uq_app_user_key_0 UNIQUE (organization_id, subject)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE event_outbox (\n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tid BIGINT GENERATED ALWAYS AS IDENTITY, \n\tscope_kind TEXT NOT NULL, \n\tscope_id UUID, \n\tkind TEXT NOT NULL, \n\tpayload JSONB NOT NULL, \n\toccurred_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_event_outbox PRIMARY KEY (id), \n\tCONSTRAINT fk_event_outbox_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_event_outbox_rule_0 CHECK (jsonb_typeof(payload)='object' AND octet_length(payload::text)<=65536), \n\tCONSTRAINT uq_event_outbox_key_0 UNIQUE (organization_id, id)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE role (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tcode TEXT NOT NULL, \n\tpermissions JSONB NOT NULL, \n\tCONSTRAINT pk_role PRIMARY KEY (id), \n\tCONSTRAINT fk_role_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_role_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT uq_role_key_0 UNIQUE (organization_id, code)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE audit_event (\n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tid BIGINT GENERATED ALWAYS AS IDENTITY, \n\tactor_id UUID, \n\taction TEXT NOT NULL, \n\ttarget_kind TEXT NOT NULL, \n\ttarget_id UUID, \n\tscope_kind TEXT NOT NULL, \n\tscope_id UUID, \n\toccurred_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tversions JSONB NOT NULL, \n\treason TEXT NOT NULL, \n\tCONSTRAINT pk_audit_event PRIMARY KEY (id), \n\tCONSTRAINT fk_audit_event_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_audit_event_actor_id FOREIGN KEY(organization_id, actor_id) REFERENCES app_user (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_audit_event_rule_0 CHECK (length(trim(reason))>0), \n\tCONSTRAINT uq_audit_event_key_0 UNIQUE (organization_id, id)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE auth_session (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tuser_id UUID NOT NULL, \n\trefresh_hash TEXT NOT NULL, \n\tcsrf_hash TEXT NOT NULL, \n\tissued_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tlast_active_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\trevoked BOOLEAN DEFAULT false NOT NULL, \n\trevoked_at TIMESTAMP WITH TIME ZONE, \n\trotation BIGINT DEFAULT 0 NOT NULL, \n\tpassword_version BIGINT NOT NULL, \n\tCONSTRAINT pk_auth_session PRIMARY KEY (id), \n\tCONSTRAINT fk_auth_session_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_auth_session_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_auth_session_user_id FOREIGN KEY(organization_id, user_id) REFERENCES app_user (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_auth_session_rule_0 CHECK (refresh_hash ~ '^[0-9a-f]{64}$' AND csrf_hash ~ '^[0-9a-f]{64}$'), \n\tCONSTRAINT ck_auth_session_rule_1 CHECK (issued_at<=last_active_at AND last_active_at<expires_at AND expires_at<=issued_at+interval '8 hours'), \n\tCONSTRAINT ck_auth_session_rule_2 CHECK ((revoked_at IS NOT NULL)=revoked), \n\tCONSTRAINT ck_auth_session_rule_3 CHECK (rotation>=0 AND password_version>=0), \n\tCONSTRAINT uq_auth_session_key_0 UNIQUE (organization_id, refresh_hash)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE import_batch (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tsource_id UUID NOT NULL, \n\tchecksum TEXT NOT NULL, \n\tkind TEXT NOT NULL, \n\tstate TEXT DEFAULT 'pending' NOT NULL, \n\ttotal_rows BIGINT DEFAULT 0 NOT NULL, \n\taccepted_rows BIGINT DEFAULT 0 NOT NULL, \n\tquarantined_rows BIGINT DEFAULT 0 NOT NULL, \n\tmanifest_hash TEXT, \n\tsummary JSONB, \n\tCONSTRAINT pk_import_batch PRIMARY KEY (id), \n\tCONSTRAINT fk_import_batch_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_import_batch_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_import_batch_source_id FOREIGN KEY(organization_id, source_id) REFERENCES source (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_import_batch_rule_0 CHECK (checksum ~ '^[0-9a-f]{64}$'), \n\tCONSTRAINT ck_import_batch_rule_1 CHECK (state IN ('pending','validating','completed','failed')), \n\tCONSTRAINT ck_import_batch_rule_2 CHECK (total_rows>=0 AND accepted_rows>=0 AND quarantined_rows>=0 AND total_rows=accepted_rows+quarantined_rows), \n\tCONSTRAINT uq_import_batch_key_0 UNIQUE (organization_id, source_id, checksum, kind)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE job (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\towner_id UUID NOT NULL, \n\tkind TEXT NOT NULL, \n\tinput_hash TEXT NOT NULL, \n\tinput JSONB NOT NULL, \n\tidempotency_key TEXT NOT NULL, \n\tstate TEXT DEFAULT 'pending' NOT NULL, \n\tattempt BIGINT DEFAULT 0 NOT NULL, \n\tmax_attempts BIGINT DEFAULT 3 NOT NULL, \n\tlease_owner TEXT, \n\tlease_expires_at TIMESTAMP WITH TIME ZONE, \n\tprogress DOUBLE PRECISION DEFAULT 0 NOT NULL, \n\terror TEXT, \n\tresult JSONB, \n\tCONSTRAINT pk_job PRIMARY KEY (id), \n\tCONSTRAINT fk_job_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_job_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_job_owner_id FOREIGN KEY(organization_id, owner_id) REFERENCES app_user (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_job_rule_0 CHECK (input_hash ~ '^[0-9a-f]{64}$'), \n\tCONSTRAINT ck_job_rule_1 CHECK (state IN ('pending','running','completed','failed','cancelled')), \n\tCONSTRAINT ck_job_rule_2 CHECK (max_attempts BETWEEN 1 AND 10 AND attempt BETWEEN 0 AND max_attempts), \n\tCONSTRAINT ck_job_rule_3 CHECK ((state='running' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL AND attempt>0) OR (state<>'running' AND lease_owner IS NULL AND lease_expires_at IS NULL)), \n\tCONSTRAINT ck_job_rule_4 CHECK (progress>=0 AND progress<=1), \n\tCONSTRAINT ck_job_rule_5 CHECK (state<>'failed' OR error IS NOT NULL), \n\tCONSTRAINT ck_job_rule_6 CHECK (state<>'completed' OR (result IS NOT NULL AND progress=1)), \n\tCONSTRAINT uq_job_key_0 UNIQUE (organization_id, kind, input_hash, idempotency_key)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE role_assignment (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tuser_id UUID NOT NULL, \n\trole_id UUID NOT NULL, \n\tscope_kind TEXT NOT NULL, \n\tfleet_id UUID, \n\tsite_id UUID, \n\tactive BOOLEAN DEFAULT true NOT NULL, \n\tCONSTRAINT pk_role_assignment PRIMARY KEY (id), \n\tCONSTRAINT fk_role_assignment_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_role_assignment_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_role_assignment_user_id FOREIGN KEY(organization_id, user_id) REFERENCES app_user (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_role_assignment_role_id FOREIGN KEY(organization_id, role_id) REFERENCES role (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_role_assignment_fleet_id FOREIGN KEY(organization_id, fleet_id) REFERENCES fleet (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_role_assignment_site_id FOREIGN KEY(organization_id, site_id) REFERENCES site (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_role_assignment_rule_0 CHECK ((scope_kind='organization' AND fleet_id IS NULL AND site_id IS NULL) OR (scope_kind='fleet' AND fleet_id IS NOT NULL AND site_id IS NULL) OR (scope_kind='site' AND site_id IS NOT NULL AND fleet_id IS NULL))\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE source_credential (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tsource_id UUID NOT NULL, \n\tcredential_ref TEXT NOT NULL, \n\tkey_id TEXT NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE, \n\trevoked BOOLEAN DEFAULT false NOT NULL, \n\tCONSTRAINT pk_source_credential PRIMARY KEY (id), \n\tCONSTRAINT fk_source_credential_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_source_credential_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_source_credential_source_id FOREIGN KEY(organization_id, source_id) REFERENCES source (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_source_credential_rule_0 CHECK (credential_ref LIKE 'secret://%%'), \n\tCONSTRAINT uq_source_credential_key_0 UNIQUE (organization_id, source_id, key_id)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE quarantine (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tbatch_id UUID NOT NULL, \n\trow_number BIGINT NOT NULL, \n\traw_row JSONB NOT NULL, \n\treason TEXT NOT NULL, \n\tCONSTRAINT pk_quarantine PRIMARY KEY (id), \n\tCONSTRAINT fk_quarantine_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_quarantine_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_quarantine_batch_id FOREIGN KEY(organization_id, batch_id) REFERENCES import_batch (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_quarantine_rule_0 CHECK (row_number>=0), \n\tCONSTRAINT uq_quarantine_key_0 UNIQUE (organization_id, batch_id, row_number)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE refresh_token_history (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tsession_id UUID NOT NULL, \n\trefresh_hash TEXT NOT NULL, \n\tconsumed_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_refresh_token_history PRIMARY KEY (id), \n\tCONSTRAINT fk_refresh_token_history_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_refresh_token_history_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_refresh_token_history_session_id FOREIGN KEY(organization_id, session_id) REFERENCES auth_session (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_refresh_token_history_rule_0 CHECK (refresh_hash ~ '^[0-9a-f]{64}$'), \n\tCONSTRAINT uq_refresh_token_history_key_0 UNIQUE (organization_id, refresh_hash)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE report_artifact (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\towner_id UUID NOT NULL, \n\tjob_id UUID NOT NULL, \n\tscenario_run_id UUID, \n\trelative_path TEXT NOT NULL, \n\tcontent_hash TEXT NOT NULL, \n\tscope_kind TEXT NOT NULL, \n\tscope_id UUID, \n\tCONSTRAINT pk_report_artifact PRIMARY KEY (id), \n\tCONSTRAINT fk_report_artifact_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_report_artifact_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_report_artifact_owner_id FOREIGN KEY(organization_id, owner_id) REFERENCES app_user (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_report_artifact_job_id FOREIGN KEY(organization_id, job_id) REFERENCES job (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_report_artifact_scenario_run_id FOREIGN KEY(organization_id, scenario_run_id) REFERENCES scenario_run (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_report_artifact_rule_0 CHECK (content_hash ~ '^[0-9a-f]{64}$'), \n\tCONSTRAINT ck_report_artifact_rule_1 CHECK (relative_path<>'' AND relative_path NOT LIKE '/%%' AND relative_path !~ '(^|/)\\.\\.(/|$)' AND position(chr(92) in relative_path)=0 AND position(':' in relative_path)=0), \n\tCONSTRAINT uq_report_artifact_key_0 UNIQUE (organization_id, relative_path)\n)\n\n"
    )
    op.execute("CREATE INDEX ix_outbox_org_sequence ON event_outbox (organization_id, id)")
    op.execute(
        "CREATE INDEX ix_audit_scope_time ON audit_event (organization_id, scope_kind, occurred_at)"
    )
    op.execute(
        "CREATE INDEX ix_audit_target ON audit_event (organization_id, target_kind, target_id)"
    )
    op.execute(
        "CREATE INDEX ix_job_pending_lease ON job (organization_id, state, lease_expires_at)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_role_assignment_scope ON role_assignment (organization_id, user_id, role_id, fleet_id, site_id) NULLS NOT DISTINCT"
    )
    op.execute(
        "ALTER TABLE maintenance_event ADD CONSTRAINT fk_maintenance_event_actor_id FOREIGN KEY(organization_id, actor_id) REFERENCES app_user (organization_id, id) ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE failure_event ADD CONSTRAINT fk_failure_event_confirmed_by FOREIGN KEY(organization_id, confirmed_by) REFERENCES app_user (organization_id, id) ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE inspection ADD CONSTRAINT fk_inspection_inspector_id FOREIGN KEY(organization_id, inspector_id) REFERENCES app_user (organization_id, id) ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE approval ADD CONSTRAINT fk_approval_actor_id FOREIGN KEY(organization_id, actor_id) REFERENCES app_user (organization_id, id) ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE stock_movement ADD CONSTRAINT fk_stock_movement_actor_id FOREIGN KEY(organization_id, actor_id) REFERENCES app_user (organization_id, id) ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE policy_version ADD CONSTRAINT fk_policy_version_actor_id FOREIGN KEY(organization_id, actor_id) REFERENCES app_user (organization_id, id) ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE model_deployment ADD CONSTRAINT fk_model_deployment_actor_id FOREIGN KEY(organization_id, actor_id) REFERENCES app_user (organization_id, id) ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE alert ADD CONSTRAINT fk_alert_acknowledged_by FOREIGN KEY(organization_id, acknowledged_by) REFERENCES app_user (organization_id, id) ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE aircraft_status_event ADD CONSTRAINT fk_aircraft_status_event_actor_id FOREIGN KEY(organization_id, actor_id) REFERENCES app_user (organization_id, id) ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE schedule_plan ADD CONSTRAINT fk_schedule_plan_approved_by FOREIGN KEY(organization_id, approved_by) REFERENCES app_user (organization_id, id) ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE scenario ADD CONSTRAINT fk_scenario_owner_id FOREIGN KEY(organization_id, owner_id) REFERENCES app_user (organization_id, id) ON DELETE RESTRICT"
    )
    op.execute(
        "CREATE TRIGGER immutable_audit_event BEFORE UPDATE OR DELETE ON audit_event FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
    op.execute(
        "CREATE TRIGGER immutable_event_outbox BEFORE UPDATE OR DELETE ON event_outbox FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
    op.execute(
        "CREATE TRIGGER immutable_refresh_token_history BEFORE UPDATE OR DELETE ON refresh_token_history FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
    op.execute(
        "CREATE TRIGGER immutable_quarantine BEFORE UPDATE OR DELETE ON quarantine FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
    op.execute(
        "GRANT INSERT ON auth_session,refresh_token_history,auth_rate_limit,job,event_outbox,audit_event TO fleetiq_app"
    )
    op.execute("GRANT UPDATE ON auth_session,auth_rate_limit TO fleetiq_app")
    op.execute("GRANT INSERT ON event_outbox,audit_event TO fleetiq_worker")
    op.execute(
        "GRANT UPDATE (state,attempt,lease_owner,lease_expires_at,progress,error,result) ON job TO fleetiq_worker"
    )
    op.execute(
        "GRANT USAGE, SELECT ON SEQUENCE event_outbox_id_seq,audit_event_id_seq TO fleetiq_app,fleetiq_worker"
    )
    op.execute(
        "REVOKE SELECT ON auth_session,refresh_token_history,auth_rate_limit FROM fleetiq_worker"
    )
    op.execute("REVOKE SELECT ON source_credential FROM fleetiq_app")
    op.execute(
        "GRANT SELECT ON auth_rate_limit,app_user,event_outbox,role,audit_event,auth_session,import_batch,job,role_assignment,source_credential,quarantine,refresh_token_history,report_artifact TO fleetiq_app, fleetiq_worker"
    )
    op.execute(
        "REVOKE SELECT ON auth_session,refresh_token_history,auth_rate_limit FROM fleetiq_worker"
    )
    op.execute("REVOKE SELECT ON source_credential FROM fleetiq_app")


def downgrade():
    op.execute("ALTER TABLE maintenance_event DROP CONSTRAINT fk_maintenance_event_actor_id")
    op.execute("ALTER TABLE failure_event DROP CONSTRAINT fk_failure_event_confirmed_by")
    op.execute("ALTER TABLE inspection DROP CONSTRAINT fk_inspection_inspector_id")
    op.execute("ALTER TABLE approval DROP CONSTRAINT fk_approval_actor_id")
    op.execute("ALTER TABLE stock_movement DROP CONSTRAINT fk_stock_movement_actor_id")
    op.execute("ALTER TABLE policy_version DROP CONSTRAINT fk_policy_version_actor_id")
    op.execute("ALTER TABLE model_deployment DROP CONSTRAINT fk_model_deployment_actor_id")
    op.execute("ALTER TABLE alert DROP CONSTRAINT fk_alert_acknowledged_by")
    op.execute(
        "ALTER TABLE aircraft_status_event DROP CONSTRAINT fk_aircraft_status_event_actor_id"
    )
    op.execute("ALTER TABLE schedule_plan DROP CONSTRAINT fk_schedule_plan_approved_by")
    op.execute("ALTER TABLE scenario DROP CONSTRAINT fk_scenario_owner_id")
    op.execute("DROP TABLE report_artifact")
    op.execute("DROP TABLE refresh_token_history")
    op.execute("DROP TABLE quarantine")
    op.execute("DROP TABLE source_credential")
    op.execute("DROP TABLE role_assignment")
    op.execute("DROP TABLE job")
    op.execute("DROP TABLE import_batch")
    op.execute("DROP TABLE auth_session")
    op.execute("DROP TABLE audit_event")
    op.execute("DROP TABLE role")
    op.execute("DROP TABLE event_outbox")
    op.execute("DROP TABLE app_user")
    op.execute("DROP TABLE auth_rate_limit")
