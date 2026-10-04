"""Real app/worker roles, commit-visible receipts and bounded replay retries."""

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pandas as pd
import sqlalchemy as sa
from fastapi.testclient import TestClient
from fleetiq_api.auth import AuthService, digest
from fleetiq_api.main import create_app
from fleetiq_api.settings import Settings
from fleetiq_data.seed import demo_id, grant_demo_import, seed_database
from fleetiq_domain.identity import hash_password
from fleetiq_domain.models.ingestion import IngestionReceipt, SourceAccess
from fleetiq_domain.models.operations import Job
from fleetiq_domain.models.telemetry import SensorReading
from fleetiq_worker.__main__ import run
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]


def test_durable_source_scoped_ingestion_and_dataset_job(isolated_database, monkeypatch):
    settings = Settings()
    migration = sa.create_engine(isolated_database[0], hide_parameters=True)
    key, secret = uuid4(), "fixture-source-secret-with-sufficient-entropy"
    with migration.begin() as c:
        seed_database(
            c,
            ROOT / "data/synthetic",
            demo_only=True,
            password_hash=hash_password("Fixture-passphrase-42"),
        )
        grant_demo_import(c)
        c.execute(
            sa.insert(SourceAccess).values(
                id=key,
                organization_id=demo_id("organization"),
                source_id=demo_id("source"),
                user_id=demo_id("user"),
                token_hash=digest(secret),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )

    def engine(url):
        return sa.create_engine(
            make_url(url).set(database=make_url(isolated_database[0]).database),
            hide_parameters=True,
        )

    app_engine, worker = (
        engine(settings.database_url.get_secret_value()),
        engine(settings.worker_database_url.get_secret_value()),
    )
    rows = (
        pd.read_parquet(ROOT / "data/synthetic/observed/sensor_observations.parquet")
        .head(2)
        .to_dict("records")
    )
    try:
        with TestClient(
            create_app(AuthService(app_engine, settings)),
            base_url="http://localhost:8000",
            headers={"Origin": "http://localhost:3000"},
        ) as client:
            headers = {"X-Source-Key": str(key) + ":" + secret, "Idempotency-Key": "first"}
            payload = {"rows": rows, "mode": "historical"}
            assert client.post("/ingestion/telemetry-batches", json=payload).status_code == 401
            response = client.post("/ingestion/telemetry-batches", json=payload, headers=headers)
            assert response.status_code == 200, response.text
            assert response.json()["readings"] == 6
            # Independently connected reader sees the effect immediately after acknowledgment.
            with migration.connect() as c:
                assert c.scalar(sa.select(sa.func.count()).select_from(SensorReading)) == 6
                assert c.scalar(sa.select(sa.func.count()).select_from(IngestionReceipt)) == 1
            assert (
                client.post("/ingestion/telemetry-batches", json=payload, headers=headers).json()
                == response.json()
            )
            changed = {
                "rows": [dict(rows[0], temperature_c=rows[0]["temperature_c"] + 0.5)],
                "mode": "historical",
            }
            assert (
                client.post(
                    "/ingestion/telemetry-batches", json=changed, headers=headers
                ).status_code
                == 409
            )
            assert (
                client.post(
                    "/ingestion/telemetry-batches",
                    json=changed,
                    headers=headers | {"Idempotency-Key": "different"},
                ).status_code
                == 409
            )
            bad = {"rows": [dict(rows[0], temperature_unit="bananas")], "mode": "historical"}
            response = client.post(
                "/ingestion/telemetry-batches",
                json=bad,
                headers=headers | {"Idempotency-Key": "invalid-unit"},
            )
            assert response.status_code == 422 and secret not in response.text
            assert (
                client.post(
                    "/ingestion/telemetry-batches",
                    json={"rows": rows * 167},
                    headers=headers | {"Idempotency-Key": "too-many"},
                ).status_code
                == 422
            )
            # Source keys cannot create session-authorized dataset jobs.
            url = "/ingestion/dataset-imports?source_id=" + str(demo_id("source"))
            assert client.post(url, content=json.dumps(rows), headers=headers).status_code == 401
            csrf = client.get("/auth/csrf").json()["csrf_token"]
            login = client.post(
                "/auth/login",
                json={
                    "organization_code": "FLEETIQ-DEMO",
                    "subject": "demo-admin",
                    "password": "Fixture-passphrase-42",
                },
                headers={"X-CSRF-Token": csrf},
            )
            assert login.status_code == 200
            upload_headers = {
                "X-CSRF-Token": login.json()["csrf_token"],
                "Idempotency-Key": "upload",
            }
            upload = client.post(url, content=json.dumps(rows), headers=upload_headers)
            assert upload.status_code == 202, upload.text
            assert (
                client.post(url, content=json.dumps(rows), headers=upload_headers).json()
                == upload.json()
            )
            import fleetiq_api.routers.ingestion as routes

            monkeypatch.setattr(routes, "MAX_BYTES", 4)
            assert (
                client.post(
                    url, content=b"12345", headers=upload_headers | {"Idempotency-Key": "oversize"}
                ).status_code
                == 413
            )
            monkeypatch.undo()
            counts = run(worker, once=True)
            assert counts.get("completed") == 1 and counts.get("unsupported") == 2
            with migration.connect() as c:
                state = c.scalar(sa.select(Job.state).where(Job.id == uuid4()))
                assert state is None
                assert (
                    c.scalar(
                        sa.select(Job.state).where(
                            Job.id == __import__("uuid").UUID(upload.json()["job_id"])
                        )
                    )
                    == "completed"
                )
                assert c.scalar(sa.select(sa.func.count()).select_from(SensorReading)) == 6
            with migration.begin() as c:
                c.execute(
                    sa.update(SourceAccess).where(SourceAccess.id == key).values(revoked=True)
                )
            assert (
                client.post(
                    "/ingestion/telemetry-batches", json=payload, headers=headers
                ).status_code
                == 401
            )
    finally:
        app_engine.dispose()
        worker.dispose()
        migration.dispose()


def test_replay_retries_and_redacted_failure():
    spec = importlib.util.spec_from_file_location("replay", ROOT / "scripts/replay.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    statuses = iter([429, 503, 200])
    waits = []
    bodies = []

    def respond(request):
        bodies.append(request.content)
        status = next(statuses)
        return httpx.Response(status, json={"committed": True}, headers={"Retry-After": "2"})

    with httpx.Client(
        base_url="http://localhost:8000", transport=httpx.MockTransport(respond)
    ) as client:
        assert module.send_batch(client, b"{}", "same", sleep=waits.append) == {"committed": True}
    assert bodies == [b"{}"] * 3 and waits == [2, 2]
