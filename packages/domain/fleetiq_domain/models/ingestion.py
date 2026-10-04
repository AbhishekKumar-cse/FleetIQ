"""Source-only hashed credentials, durable HTTP receipts and per-source watermarks."""

from fleetiq_domain.models import backfill  # noqa: F401
from fleetiq_domain.models.schema import entity

SourceAccess = entity(
    "source_access",
    {
        "source_id": "uuid:required",
        "user_id": "uuid:required",
        "token_hash": "text:required",
        "expires_at": "time:required",
        "revoked": "bool:required:false",
    },
    refs={"source_id": "source", "user_id": "app_user"},
    checks=("token_hash ~ '^[0-9a-f]{64}$'",),
)
IngestionReceipt = entity(
    "ingestion_receipt",
    {
        "source_id": "uuid:required",
        "request_key": "text:required",
        "input_hash": "text:required",
        "response": "json:required",
        "route": "text:required",
    },
    refs={"source_id": "source"},
    unique=(("source_id", "route", "request_key"),),
    checks=("input_hash ~ '^[0-9a-f]{64}$'", "length(request_key) BETWEEN 1 AND 128"),
)
SourceWatermark = entity(
    "source_watermark",
    {
        "source_id": "uuid:required",
        "component_id": "uuid:required",
        "latest_at": "time:required",
    },
    refs={"source_id": "source", "component_id": "component"},
    unique=(("source_id", "component_id"),),
)
EXTRA = [
    "CREATE TRIGGER immutable_ingestion_receipt BEFORE UPDATE OR DELETE ON ingestion_receipt FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()"
]
POST_GRANTS = [
    "GRANT INSERT ON ingestion_receipt,source_watermark TO fleetiq_app",
    "GRANT UPDATE(latest_at) ON source_watermark TO fleetiq_app",
    "GRANT INSERT ON source_event_receipt,sensor_reading,import_batch,quarantine TO fleetiq_worker",
    "GRANT UPDATE(state,total_rows,accepted_rows,quarantined_rows,summary) ON import_batch TO fleetiq_worker",
]
