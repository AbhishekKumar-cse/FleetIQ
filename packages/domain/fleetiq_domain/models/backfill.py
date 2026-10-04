"""Retained correction requests and a coalesced current assessment pointer."""

from fleetiq_domain.models import jobs  # noqa: F401
from fleetiq_domain.models.schema import entity

BackfillRequest = entity(
    "backfill_request",
    {
        "component_id": "uuid:required",
        "as_of": "time:required",
        "source_cutoff": "time:required",
        "input_hash": "text:required",
        "historical": "bool:required",
        "actor_id": "uuid:required",
    },
    refs={"component_id": "component", "actor_id": "app_user"},
    checks=("input_hash ~ '^[0-9a-f]{64}$'",),
    unique=(("component_id", "as_of", "input_hash", "historical"),),
)
AssessmentCursor = entity(
    "assessment_cursor",
    {"component_id": "uuid:required", "request_id": "uuid:required", "job_id": "uuid:required"},
    refs={"component_id": "component", "request_id": "backfill_request", "job_id": "job"},
    unique=(("component_id",),),
)
EXTRA = [
    "CREATE TRIGGER immutable_backfill_request BEFORE UPDATE OR DELETE ON backfill_request FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
]
POST_GRANTS = [
    "GRANT INSERT ON backfill_request,assessment_cursor TO fleetiq_app",
    "GRANT UPDATE(request_id,job_id) ON assessment_cursor TO fleetiq_app",
    "GRANT INSERT ON feature_snapshot,prediction TO fleetiq_worker",
]
