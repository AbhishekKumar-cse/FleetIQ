"""Bounded durable ingestion: source keys can send telemetry; sessions can upload datasets."""

import hashlib
import hmac
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request
from fleetiq_data.http_ingestion import approved_source, ingest_rows, persist_receipt, receipt
from fleetiq_data.quality.dedup import DuplicateConflict, content_hash
from fleetiq_data.storage import store_raw
from fleetiq_domain.authorization import Forbidden, Principal
from fleetiq_domain.identity import AuthenticationError
from fleetiq_domain.models.ingestion import IngestionReceipt, SourceAccess
from fleetiq_domain.models.operations import enqueue_job
from sqlalchemy.exc import OperationalError

from fleetiq_api.auth import digest
from fleetiq_api.routers.auth import access, csrf_check, get_auth_service

router = APIRouter(prefix="/ingestion", tags=["ingestion"])
ROOT = Path(__file__).resolve().parents[4]
MAX_BYTES = 50 * 1024 * 1024


def session(c, request, service):
    row = service.authenticate(c, access(request))
    if not hmac.compare_digest(digest(csrf_check(request, service)), row["csrf_hash"]):
        raise HTTPException(403, "CSRF rejected")
    return Principal(row["organization_id"], row["user_id"])


def source_key(c, request):
    token = request.headers.get("x-source-key", "")
    try:
        key, secret = token.split(":")
        identifier = UUID(key)
    except ValueError:
        raise HTTPException(401, "Source authentication required") from None
    row = (
        c.execute(
            sa.select(SourceAccess.__table__).where(
                SourceAccess.id == identifier,
                SourceAccess.revoked.is_(False),
                SourceAccess.expires_at > datetime.now(UTC),
            )
        )
        .mappings()
        .first()
    )
    if not row or not hmac.compare_digest(digest(secret), row["token_hash"]):
        raise HTTPException(401, "Source authentication required")
    return Principal(row["organization_id"], row["user_id"]), row["source_id"]


def request_key(request):
    key = request.headers.get("idempotency-key", "")
    if not 1 <= len(key) <= 128:
        raise HTTPException(422, "Idempotency-Key of 1–128 characters required")
    return key


async def body(request):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_BYTES:
            raise HTTPException(413, "Upload exceeds 50 MiB")
        data.extend(chunk)
    return bytes(data)


def translate(error):
    if isinstance(error, DuplicateConflict):
        return HTTPException(409, "Idempotency conflict")
    if isinstance(error, Forbidden):
        return HTTPException(403, "Access denied")
    if isinstance(error, AuthenticationError):
        return HTTPException(401, "Authentication required")
    if isinstance(error, OperationalError):
        return HTTPException(503, "Ingestion temporarily unavailable", headers={"Retry-After": "2"})
    return HTTPException(422, "Ingestion contract rejected")


@router.post("/telemetry-batches")
async def telemetry(request: Request):
    service = get_auth_service(request)
    try:
        with service.engine.begin() as c:
            principal, source = source_key(c, request)
            approved_source(c, principal, source)
        raw = await body(request)
        payload = json.loads(raw)
        if not isinstance(payload, dict) or set(payload) - {"rows", "mode"}:
            raise ValueError("Invalid envelope")
        key, checksum = request_key(request), content_hash(payload)
        # Keep the original observation contract as immutable raw evidence, without headers/secrets.
        staging = ROOT / "data/raw/uploads"
        staging.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=staging, suffix=".json")
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(payload.get("rows"), handle, allow_nan=False)
            store_raw(Path(name), input_root=staging, vault_root=ROOT / "data/raw/imports")
        finally:
            Path(name).unlink(missing_ok=True)
        with service.engine.begin() as c:
            principal, source = source_key(c, request)
            approved_source(c, principal, source)
            old = receipt(c, principal, source, key, "telemetry", checksum)
            if old is not None:
                result = old
            else:
                c.execute(
                    sa.text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                    {"key": f"rate:{source}:{principal.organization_id}"},
                )
                recent = c.scalar(
                    sa.select(sa.func.count())
                    .select_from(IngestionReceipt)
                    .where(
                        IngestionReceipt.organization_id == principal.organization_id,
                        IngestionReceipt.source_id == source,
                        IngestionReceipt.created_at > datetime.now(UTC) - timedelta(minutes=1),
                    )
                )
                if recent >= 120:
                    raise HTTPException(
                        429, "Source batch rate exceeded", headers={"Retry-After": "2"}
                    )
                result = ingest_rows(
                    c, principal, source, payload["rows"], mode=payload.get("mode", "replay")
                )
                persist_receipt(c, principal, source, key, "telemetry", checksum, result)
        # The transaction has committed before any acknowledgment is constructed.
        return result
    except (
        ValueError,
        KeyError,
        TypeError,
        Forbidden,
        AuthenticationError,
        OperationalError,
    ) as error:
        raise translate(error) from None


@router.post("/dataset-imports", status_code=202)
async def dataset(request: Request, source_id: UUID, format: str = "json"):
    service = get_auth_service(request)
    try:
        if format not in {"json", "csv", "parquet"}:
            raise ValueError("Unsupported format")
        with service.engine.begin() as c:
            principal = session(c, request, service)
            approved_source(c, principal, source_id)
        raw = await body(request)
        key = request_key(request)
        checksum = content_hash(
            {"source": str(source_id), "format": format, "sha256": hashlib.sha256(raw).hexdigest()}
        )
        staging = ROOT / "data/raw/uploads"
        staging.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=staging, suffix="." + format)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
            stored, file_hash = store_raw(
                Path(name), input_root=staging, vault_root=ROOT / "data/raw/imports"
            )
        finally:
            Path(name).unlink(missing_ok=True)
        with service.engine.begin() as c:
            principal = session(c, request, service)
            approved_source(c, principal, source_id)
            result = receipt(c, principal, source_id, key, "dataset", checksum)
            if result is None:
                job = enqueue_job(
                    c,
                    dict(
                        organization_id=principal.organization_id,
                        owner_id=principal.user_id,
                        kind="dataset.import",
                        input_hash=checksum,
                        idempotency_key=key,
                        input={
                            "source_id": str(source_id),
                            "path": str(stored.relative_to(ROOT)),
                            "sha256": file_hash,
                        },
                    ),
                    actor_id=principal.user_id,
                    reason="Accepted bounded dataset upload",
                )
                result = {"job_id": str(job), "state": "pending", "bytes": len(raw)}
                persist_receipt(c, principal, source_id, key, "dataset", checksum, result)
        return result
    except (
        ValueError,
        KeyError,
        TypeError,
        Forbidden,
        AuthenticationError,
        OperationalError,
    ) as error:
        raise translate(error) from None
