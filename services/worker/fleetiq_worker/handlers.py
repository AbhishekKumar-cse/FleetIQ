"""Only registered real handlers complete; future ML/scenario handlers abstain."""

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from fleetiq_domain.models.operations import ImportBatch


@dataclass(frozen=True)
class Outcome:
    result: dict
    unsupported: bool = False
    effect: object = None


def dispatch(c, job):
    if job["kind"] in {"history.backfill", "current.feature"}:
        from fleetiq_worker.backfill_handler import revise

        return Outcome(
            {
                "status": "unsupported",
                "reason": "Revisions retained; registered recomputation required",
            },
            True,
            lambda transaction: revise(transaction, job),
        )
    if job["kind"] != "ingestion.summary":
        return Outcome({"status": "unsupported", "reason": "Handler is not implemented"}, True)
    inputs = job["input"]
    batch = (
        c.execute(
            sa.select(ImportBatch.__table__).where(
                ImportBatch.id == UUID(inputs["batch_id"]),
                ImportBatch.organization_id == job["organization_id"],
            )
        )
        .mappings()
        .one()
    )
    summary = batch["summary"]
    through = inputs["through_row"]
    if (
        type(through) is not int
        or through < 0
        or summary["processed"] < through
        or summary["processed"]
        != sum(summary[k] for k in ("accepted", "flagged", "rejected", "duplicate"))
    ):
        raise ValueError("Import progress is inconsistent")
    return Outcome(
        {
            "status": "verified",
            "batch_id": str(batch["id"]),
            "requested_through_row": through,
            "verified_through_row": summary["processed"],
            "counts": {k: v for k, v in summary.items() if k != "streams"},
        }
    )
