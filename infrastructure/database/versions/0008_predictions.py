"""Frozen predictions schema."""

from alembic import op

revision = "0008_predictions"
down_revision = "0007a_schema_contract"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "\nCREATE TABLE model_deployment (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\ttrack TEXT NOT NULL, \n\ttask TEXT NOT NULL, \n\tunit TEXT NOT NULL, \n\thorizon NUMERIC(18, 6) NOT NULL, \n\tfeature_version TEXT NOT NULL, \n\tmodel_version TEXT NOT NULL, \n\tcalibration_version TEXT NOT NULL, \n\tbundle_hash TEXT NOT NULL, \n\tapplicability JSONB NOT NULL, \n\teffective_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tactor_id UUID, \n\tCONSTRAINT pk_model_deployment PRIMARY KEY (id), \n\tCONSTRAINT fk_model_deployment_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_model_deployment_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT ck_model_deployment_rule_0 CHECK (track IN ('cmapss_benchmark','synthetic_engine_demo')), \n\tCONSTRAINT ck_model_deployment_rule_1 CHECK ((task='anomaly' AND unit='score' AND horizon=0) OR\n(task='rul' AND horizon=0 AND ((track='cmapss_benchmark' AND unit='cycles') OR\n(track='synthetic_engine_demo' AND unit='operating_hours'))) OR\n(task='failure_risk' AND horizon>0 AND horizon<'Infinity'::numeric AND\n((track='cmapss_benchmark' AND unit='cycles') OR\n(track='synthetic_engine_demo' AND unit='operating_hours')))), \n\tCONSTRAINT ck_model_deployment_rule_2 CHECK (bundle_hash ~ '^[0-9a-f]{64}$'), \n\tCONSTRAINT uq_model_deployment_key_0 UNIQUE (organization_id, bundle_hash, task, track, horizon, unit)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE policy_version (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tcode TEXT NOT NULL, \n\tversion TEXT NOT NULL, \n\tcontent_hash TEXT NOT NULL, \n\tapplicability JSONB NOT NULL, \n\teffective_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tactor_id UUID, \n\tCONSTRAINT pk_policy_version PRIMARY KEY (id), \n\tCONSTRAINT fk_policy_version_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_policy_version_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT ck_policy_version_rule_0 CHECK (content_hash ~ '^[0-9a-f]{64}$'), \n\tCONSTRAINT uq_policy_version_key_0 UNIQUE (organization_id, code, version)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE health_score (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\taircraft_id UUID NOT NULL, \n\tas_of TIMESTAMP WITH TIME ZONE NOT NULL, \n\tscore DOUBLE PRECISION, \n\tcategory TEXT NOT NULL, \n\tcoverage TEXT NOT NULL, \n\tlimiting_component_id UUID, \n\tpolicy_version_id UUID NOT NULL, \n\tinput_hash TEXT NOT NULL, \n\tCONSTRAINT pk_health_score PRIMARY KEY (id), \n\tCONSTRAINT fk_health_score_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_health_score_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_health_score_aircraft_id FOREIGN KEY(organization_id, aircraft_id) REFERENCES aircraft (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_health_score_limiting_component_id FOREIGN KEY(organization_id, limiting_component_id) REFERENCES component (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_health_score_policy_version_id FOREIGN KEY(organization_id, policy_version_id) REFERENCES policy_version (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_health_score_rule_0 CHECK (score IS NULL OR (score >= 0 AND score <= 100)), \n\tCONSTRAINT ck_health_score_rule_1 CHECK (coverage IN ('full','partial','unsupported','stale')), \n\tCONSTRAINT ck_health_score_rule_2 CHECK (category IN ('healthy','watch','critical','unknown')), \n\tCONSTRAINT ck_health_score_rule_3 CHECK (coverage NOT IN ('unsupported','stale') OR (score IS NULL AND category='unknown')), \n\tCONSTRAINT ck_health_score_rule_4 CHECK (input_hash ~ '^[0-9a-f]{64}$'), \n\tCONSTRAINT uq_health_score_key_0 UNIQUE (organization_id, aircraft_id, as_of, policy_version_id, input_hash)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE feature_snapshot (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tcomponent_id UUID NOT NULL, \n\tinstallation_id UUID, \n\tas_of TIMESTAMP WITH TIME ZONE NOT NULL, \n\twindow_start TIMESTAMP WITH TIME ZONE NOT NULL, \n\twindow_end TIMESTAMP WITH TIME ZONE NOT NULL, \n\tfeature_version TEXT NOT NULL, \n\tinput_hash TEXT NOT NULL, \n\ttrack TEXT NOT NULL, \n\tvector JSONB NOT NULL, \n\tquality JSONB NOT NULL, \n\tCONSTRAINT pk_feature_snapshot PRIMARY KEY (id), \n\tCONSTRAINT fk_feature_snapshot_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_feature_snapshot_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_feature_snapshot_component_id FOREIGN KEY(organization_id, component_id) REFERENCES component (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_feature_snapshot_installation_id FOREIGN KEY(organization_id, installation_id) REFERENCES installation (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_feature_snapshot_rule_0 CHECK (track IN ('cmapss_benchmark','synthetic_engine_demo')), \n\tCONSTRAINT ck_feature_snapshot_rule_1 CHECK (window_start < window_end AND window_end <= as_of), \n\tCONSTRAINT ck_feature_snapshot_rule_2 CHECK (track <> 'synthetic_engine_demo' OR installation_id IS NOT NULL), \n\tCONSTRAINT ck_feature_snapshot_rule_3 CHECK (input_hash ~ '^[0-9a-f]{64}$'), \n\tCONSTRAINT uq_feature_snapshot_key_0 UNIQUE (organization_id, component_id, as_of, input_hash, feature_version, track)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE prediction (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tfeature_snapshot_id UUID NOT NULL, \n\tcomponent_id UUID NOT NULL, \n\tdeployment_id UUID NOT NULL, \n\tas_of TIMESTAMP WITH TIME ZONE NOT NULL, \n\ttask TEXT NOT NULL, \n\ttrack TEXT NOT NULL, \n\thorizon NUMERIC(18, 6) NOT NULL, \n\tunit TEXT NOT NULL, \n\tinput_hash TEXT NOT NULL, \n\tfeature_version TEXT NOT NULL, \n\tmodel_version TEXT NOT NULL, \n\tcalibration_version TEXT NOT NULL, \n\tbundle_hash TEXT NOT NULL, \n\toutput JSONB, \n\tuncertainty JSONB, \n\tquality JSONB NOT NULL, \n\tood BOOLEAN DEFAULT false NOT NULL, \n\tcoverage TEXT NOT NULL, \n\texplanation_status TEXT NOT NULL, \n\tCONSTRAINT pk_prediction PRIMARY KEY (id), \n\tCONSTRAINT fk_prediction_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_prediction_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_prediction_feature_snapshot_id FOREIGN KEY(organization_id, feature_snapshot_id) REFERENCES feature_snapshot (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_prediction_component_id FOREIGN KEY(organization_id, component_id) REFERENCES component (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_prediction_deployment_id FOREIGN KEY(organization_id, deployment_id) REFERENCES model_deployment (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_prediction_rule_0 CHECK (track IN ('cmapss_benchmark','synthetic_engine_demo')), \n\tCONSTRAINT ck_prediction_rule_1 CHECK ((task='anomaly' AND unit='score' AND horizon=0) OR\n(task='rul' AND horizon=0 AND ((track='cmapss_benchmark' AND unit='cycles') OR\n(track='synthetic_engine_demo' AND unit='operating_hours'))) OR\n(task='failure_risk' AND horizon>0 AND horizon<'Infinity'::numeric AND\n((track='cmapss_benchmark' AND unit='cycles') OR\n(track='synthetic_engine_demo' AND unit='operating_hours')))), \n\tCONSTRAINT ck_prediction_rule_2 CHECK (input_hash ~ '^[0-9a-f]{64}$'), \n\tCONSTRAINT ck_prediction_rule_3 CHECK (bundle_hash ~ '^[0-9a-f]{64}$'), \n\tCONSTRAINT ck_prediction_rule_4 CHECK (coverage IN ('full','partial','unsupported','stale')), \n\tCONSTRAINT ck_prediction_rule_5 CHECK (coverage NOT IN ('unsupported','stale') OR output IS NULL), \n\tCONSTRAINT ck_prediction_rule_6 CHECK (output IS NULL OR output <> 'null'::jsonb), \n\tCONSTRAINT ck_prediction_rule_7 CHECK (explanation_status IN ('available','pending','unsupported','failed')), \n\tCONSTRAINT uq_prediction_key_0 UNIQUE (organization_id, component_id, as_of, task, track, horizon, unit, input_hash, bundle_hash)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE alert (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\torganization_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\taircraft_id UUID, \n\tcomponent_id UUID, \n\tprediction_id UUID, \n\tkind TEXT NOT NULL, \n\tseverity TEXT NOT NULL, \n\tstate TEXT DEFAULT 'open' NOT NULL, \n\treason TEXT NOT NULL, \n\tcondition_key TEXT NOT NULL, \n\tas_of TIMESTAMP WITH TIME ZONE NOT NULL, \n\tpolicy_version_id UUID NOT NULL, \n\tacknowledged_by UUID, \n\tacknowledged_at TIMESTAMP WITH TIME ZONE, \n\tCONSTRAINT pk_alert PRIMARY KEY (id), \n\tCONSTRAINT fk_alert_organization_id_organization FOREIGN KEY(organization_id) REFERENCES organization (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_alert_org_id UNIQUE (organization_id, id), \n\tCONSTRAINT fk_alert_aircraft_id FOREIGN KEY(organization_id, aircraft_id) REFERENCES aircraft (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_alert_component_id FOREIGN KEY(organization_id, component_id) REFERENCES component (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_alert_prediction_id FOREIGN KEY(organization_id, prediction_id) REFERENCES prediction (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_alert_policy_version_id FOREIGN KEY(organization_id, policy_version_id) REFERENCES policy_version (organization_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_alert_rule_0 CHECK (aircraft_id IS NOT NULL OR component_id IS NOT NULL), \n\tCONSTRAINT ck_alert_rule_1 CHECK (severity IN ('info','warning','critical')), \n\tCONSTRAINT ck_alert_rule_2 CHECK (state IN ('open','acknowledged','resolved')), \n\tCONSTRAINT ck_alert_rule_3 CHECK ((acknowledged_at IS NULL) = (acknowledged_by IS NULL)), \n\tCONSTRAINT ck_alert_rule_4 CHECK (state <> 'acknowledged' OR acknowledged_at IS NOT NULL)\n)\n\n"
    )
    op.execute(
        "CREATE INDEX ix_prediction_component_time ON prediction (organization_id, component_id, as_of)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_active_alert_condition ON alert (organization_id, condition_key) WHERE state <> 'resolved'"
    )
    op.execute(
        "ALTER TABLE recommendation_evidence ADD CONSTRAINT fk_recommendation_evidence_prediction_id FOREIGN KEY(organization_id, prediction_id) REFERENCES prediction (organization_id, id) ON DELETE RESTRICT"
    )
    op.execute(
        "CREATE FUNCTION fleetiq_validate_prediction() RETURNS trigger LANGUAGE plpgsql AS $$\nBEGIN IF NOT EXISTS(SELECT 1 FROM feature_snapshot f JOIN model_deployment m ON\nm.organization_id=f.organization_id WHERE f.id=NEW.feature_snapshot_id\nAND f.organization_id=NEW.organization_id AND m.id=NEW.deployment_id\nAND (f.component_id,f.as_of,f.track,f.feature_version,f.input_hash)=\n(NEW.component_id,NEW.as_of,NEW.track,NEW.feature_version,NEW.input_hash)\nAND (m.task,m.track,m.unit,m.horizon,m.feature_version,m.model_version,m.calibration_version,m.bundle_hash)=\n(NEW.task,NEW.track,NEW.unit,NEW.horizon,NEW.feature_version,NEW.model_version,NEW.calibration_version,NEW.bundle_hash)\nAND m.effective_at<=NEW.as_of) THEN RAISE EXCEPTION 'prediction signature/provenance mismatch'; END IF;\nRETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER validate_prediction BEFORE INSERT ON prediction FOR EACH ROW EXECUTE FUNCTION fleetiq_validate_prediction()"
    )
    op.execute(
        "CREATE FUNCTION fleetiq_validate_features() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN\nIF NEW.installation_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM installation i WHERE\ni.id=NEW.installation_id AND i.organization_id=NEW.organization_id AND i.component_id=NEW.component_id\nAND NEW.window_start>=i.installed_at AND (i.removed_at IS NULL OR NEW.window_end<=i.removed_at))\nTHEN RAISE EXCEPTION 'feature window outside component installation'; END IF; RETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER validate_features BEFORE INSERT ON feature_snapshot FOR EACH ROW EXECUTE FUNCTION fleetiq_validate_features()"
    )
    op.execute(
        "CREATE TRIGGER immutable_feature_snapshot BEFORE UPDATE OR DELETE ON feature_snapshot FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
    op.execute(
        "CREATE TRIGGER immutable_prediction BEFORE UPDATE OR DELETE ON prediction FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
    op.execute(
        "CREATE TRIGGER immutable_health_score BEFORE UPDATE OR DELETE ON health_score FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
    op.execute(
        "CREATE TRIGGER immutable_policy_version BEFORE UPDATE OR DELETE ON policy_version FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
    op.execute(
        "CREATE TRIGGER immutable_model_deployment BEFORE UPDATE OR DELETE ON model_deployment FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
    op.execute(
        "GRANT SELECT ON model_deployment,policy_version,health_score,feature_snapshot,prediction,alert TO fleetiq_app, fleetiq_worker"
    )


def downgrade():
    op.execute(
        "ALTER TABLE recommendation_evidence DROP CONSTRAINT fk_recommendation_evidence_prediction_id"
    )
    op.execute("DROP TRIGGER validate_prediction ON prediction")
    op.execute("DROP FUNCTION fleetiq_validate_prediction()")
    op.execute("DROP TRIGGER validate_features ON feature_snapshot")
    op.execute("DROP FUNCTION fleetiq_validate_features()")
    op.execute("DROP TABLE alert")
    op.execute("DROP TABLE prediction")
    op.execute("DROP TABLE feature_snapshot")
    op.execute("DROP TABLE health_score")
    op.execute("DROP TABLE policy_version")
    op.execute("DROP TABLE model_deployment")
