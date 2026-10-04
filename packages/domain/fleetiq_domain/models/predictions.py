"""Immutable typed model evidence; unsupported output is explicit and nullable."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import AddConstraint

from fleetiq_domain.models.schema import entity
from fleetiq_domain.models.work import RecommendationEvidence

TRACK = "track IN ('cmapss_benchmark','synthetic_engine_demo')"
HASH = "{field} ~ '^[0-9a-f]{{64}}$'"
SIGNATURE = """(task='anomaly' AND unit='score' AND horizon=0) OR
(task='rul' AND horizon=0 AND ((track='cmapss_benchmark' AND unit='cycles') OR
(track='synthetic_engine_demo' AND unit='operating_hours'))) OR
(task='failure_risk' AND horizon>0 AND horizon<'Infinity'::numeric AND
((track='cmapss_benchmark' AND unit='cycles') OR
(track='synthetic_engine_demo' AND unit='operating_hours')))"""
PolicyVersion = entity(
    "policy_version",
    {
        "code": "text:required",
        "version": "text:required",
        "content_hash": "text:required",
        "applicability": "json:required",
        "effective_at": "time:required",
        "actor_id": "uuid:optional",
    },
    checks=(HASH.format(field="content_hash"),),
    unique=(("code", "version"),),
)
ModelDeployment = entity(
    "model_deployment",
    {
        "track": "text:required",
        "task": "text:required",
        "unit": "text:required",
        "horizon": "number:required",
        "feature_version": "text:required",
        "model_version": "text:required",
        "calibration_version": "text:required",
        "bundle_hash": "text:required",
        "applicability": "json:required",
        "effective_at": "time:required",
        "actor_id": "uuid:optional",
    },
    checks=(TRACK, SIGNATURE, HASH.format(field="bundle_hash")),
    unique=(("bundle_hash", "task", "track", "horizon", "unit"),),
)
FeatureSnapshot = entity(
    "feature_snapshot",
    {
        "component_id": "uuid:required",
        "installation_id": "uuid:optional",
        "as_of": "time:required",
        "window_start": "time:required",
        "window_end": "time:required",
        "feature_version": "text:required",
        "input_hash": "text:required",
        "track": "text:required",
        "vector": "json:required",
        "quality": "json:required",
    },
    refs={"component_id": "component", "installation_id": "installation"},
    checks=(
        TRACK,
        "window_start < window_end AND window_end <= as_of",
        "track <> 'synthetic_engine_demo' OR installation_id IS NOT NULL",
        HASH.format(field="input_hash"),
    ),
    unique=(("component_id", "as_of", "input_hash", "feature_version", "track"),),
)
Prediction = entity(
    "prediction",
    {
        "feature_snapshot_id": "uuid:required",
        "component_id": "uuid:required",
        "deployment_id": "uuid:required",
        "as_of": "time:required",
        "task": "text:required",
        "track": "text:required",
        "horizon": "number:required",
        "unit": "text:required",
        "input_hash": "text:required",
        "feature_version": "text:required",
        "model_version": "text:required",
        "calibration_version": "text:required",
        "bundle_hash": "text:required",
        "output": "json:optional",
        "uncertainty": "json:optional",
        "quality": "json:required",
        "ood": "bool:required:false",
        "coverage": "text:required",
        "explanation_status": "text:required",
    },
    refs={
        "feature_snapshot_id": "feature_snapshot",
        "component_id": "component",
        "deployment_id": "model_deployment",
    },
    checks=(
        TRACK,
        SIGNATURE,
        HASH.format(field="input_hash"),
        HASH.format(field="bundle_hash"),
        "coverage IN ('full','partial','unsupported','stale')",
        "coverage NOT IN ('unsupported','stale') OR output IS NULL",
        "output IS NULL OR output <> 'null'::jsonb",
        "explanation_status IN ('available','pending','unsupported','failed')",
    ),
    unique=(
        ("component_id", "as_of", "task", "track", "horizon", "unit", "input_hash", "bundle_hash"),
    ),
)
HealthScore = entity(
    "health_score",
    {
        "aircraft_id": "uuid:required",
        "as_of": "time:required",
        "score": "float:optional",
        "category": "text:required",
        "coverage": "text:required",
        "limiting_component_id": "uuid:optional",
        "policy_version_id": "uuid:required",
        "input_hash": "text:required",
    },
    refs={
        "aircraft_id": "aircraft",
        "limiting_component_id": "component",
        "policy_version_id": "policy_version",
    },
    checks=(
        "score IS NULL OR (score >= 0 AND score <= 100)",
        "coverage IN ('full','partial','unsupported','stale')",
        "category IN ('healthy','watch','critical','unknown')",
        "coverage NOT IN ('unsupported','stale') OR (score IS NULL AND category='unknown')",
        HASH.format(field="input_hash"),
    ),
    unique=(("aircraft_id", "as_of", "policy_version_id", "input_hash"),),
)
Alert = entity(
    "alert",
    {
        "aircraft_id": "uuid:optional",
        "component_id": "uuid:optional",
        "prediction_id": "uuid:optional",
        "kind": "text:required",
        "severity": "text:required",
        "state": "text:required:'open'",
        "reason": "text:required",
        "condition_key": "text:required",
        "as_of": "time:required",
        "policy_version_id": "uuid:required",
        "acknowledged_by": "uuid:optional",
        "acknowledged_at": "time:optional",
    },
    refs={
        "aircraft_id": "aircraft",
        "component_id": "component",
        "prediction_id": "prediction",
        "policy_version_id": "policy_version",
    },
    checks=(
        "aircraft_id IS NOT NULL OR component_id IS NOT NULL",
        "severity IN ('info','warning','critical')",
        "state IN ('open','acknowledged','resolved')",
        "(acknowledged_at IS NULL) = (acknowledged_by IS NULL)",
        "state <> 'acknowledged' OR acknowledged_at IS NOT NULL",
    ),
)
sa.Index(
    "uq_active_alert_condition",
    Alert.organization_id,
    Alert.condition_key,
    unique=True,
    postgresql_where=sa.text("state <> 'resolved'"),
)
sa.Index(
    "ix_prediction_component_time",
    Prediction.organization_id,
    Prediction.component_id,
    Prediction.as_of,
)
constraint = sa.ForeignKeyConstraint(
    ["organization_id", "prediction_id"],
    ["prediction.organization_id", "prediction.id"],
    name="fk_recommendation_evidence_prediction_id",
    ondelete="RESTRICT",
)
RecommendationEvidence.__table__.append_constraint(constraint)
EXTRA = [
    str(AddConstraint(constraint).compile(dialect=dialect())),
    """CREATE FUNCTION fleetiq_validate_prediction() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN IF NOT EXISTS(SELECT 1 FROM feature_snapshot f JOIN model_deployment m ON
m.organization_id=f.organization_id WHERE f.id=NEW.feature_snapshot_id
AND f.organization_id=NEW.organization_id AND m.id=NEW.deployment_id
AND (f.component_id,f.as_of,f.track,f.feature_version,f.input_hash)=
(NEW.component_id,NEW.as_of,NEW.track,NEW.feature_version,NEW.input_hash)
AND (m.task,m.track,m.unit,m.horizon,m.feature_version,m.model_version,m.calibration_version,m.bundle_hash)=
(NEW.task,NEW.track,NEW.unit,NEW.horizon,NEW.feature_version,NEW.model_version,NEW.calibration_version,NEW.bundle_hash)
AND m.effective_at<=NEW.as_of) THEN RAISE EXCEPTION 'prediction signature/provenance mismatch'; END IF;
RETURN NEW; END $$""",
    "CREATE TRIGGER validate_prediction BEFORE INSERT ON prediction FOR EACH ROW EXECUTE FUNCTION fleetiq_validate_prediction()",
    """CREATE FUNCTION fleetiq_validate_features() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
IF NEW.installation_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM installation i WHERE
i.id=NEW.installation_id AND i.organization_id=NEW.organization_id AND i.component_id=NEW.component_id
AND NEW.window_start>=i.installed_at AND (i.removed_at IS NULL OR NEW.window_end<=i.removed_at))
THEN RAISE EXCEPTION 'feature window outside component installation'; END IF; RETURN NEW; END $$""",
    "CREATE TRIGGER validate_features BEFORE INSERT ON feature_snapshot FOR EACH ROW EXECUTE FUNCTION fleetiq_validate_features()",
]
for table in (
    "feature_snapshot",
    "prediction",
    "health_score",
    "policy_version",
    "model_deployment",
):
    EXTRA.append(
        f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
    )
DOWN_EXTRA = [
    "ALTER TABLE recommendation_evidence DROP CONSTRAINT fk_recommendation_evidence_prediction_id",
    "DROP TRIGGER validate_prediction ON prediction",
    "DROP FUNCTION fleetiq_validate_prediction()",
    "DROP TRIGGER validate_features ON feature_snapshot",
    "DROP FUNCTION fleetiq_validate_features()",
]
