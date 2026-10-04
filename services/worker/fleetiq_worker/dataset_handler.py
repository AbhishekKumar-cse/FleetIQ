"""Restartable import chunks fenced on the same worker-role transaction."""

import hashlib
from pathlib import Path
from uuid import UUID

from fleetiq_data.importer import import_file
from fleetiq_domain.authorization import Principal

from fleetiq_worker.handlers import Outcome
from fleetiq_worker.jobs import _owned

ROOT = Path(__file__).resolve().parents[3]


def import_dataset(engine, lease):
    job = lease.job
    path = (ROOT / job["input"]["path"]).resolve()
    if not path.is_relative_to((ROOT / "data/raw/imports").resolve()):
        raise ValueError("Unapproved import path")
    if hashlib.sha256(path.read_bytes()).hexdigest() != job["input"]["sha256"]:
        raise ValueError("Import evidence changed")
    result = import_file(
        engine,
        Principal(job["organization_id"], job["owner_id"]),
        UUID(job["input"]["source_id"]),
        path,
        input_root=ROOT / "data/raw/imports",
        vault_root=ROOT / "data/raw/imports",
        batch_size=100,
        enqueue_followups=False,
        before_commit=lambda c, _: _owned(c, lease, None),
    )
    return Outcome({"status": "verified", "counts": result})
