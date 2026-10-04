"""Immutable, unique logical worker results shared with API/domain code."""

from fleetiq_domain.models.operations import Job  # noqa: F401
from fleetiq_domain.models.schema import entity

JobResult = entity(
    "job_result",
    {
        "job_id": "uuid:required",
        "result_key": "uuid:required",
        "attempt": "int:required",
        "state": "text:required",
        "payload": "json:required",
    },
    refs={"job_id": "job"},
    checks=(
        "attempt BETWEEN 1 AND 6",
        "state IN ('completed','unsupported')",
        "jsonb_typeof(payload)='object'",
    ),
    unique=(("job_id",), ("result_key",)),
)
