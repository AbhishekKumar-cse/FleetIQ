"""Real worker grants, native inference, atomic effects, replay and outage history."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from fleetiq_api.settings import Settings
from fleetiq_data.cmapss import parse_trajectory
from fleetiq_domain.models.operations import EventOutbox, Job, enqueue_job
from fleetiq_domain.models.predictions import FeatureSnapshot, ModelDeployment, Prediction
from fleetiq_evaluation.splits import ROOT
from fleetiq_inference.serving import create_app
from fleetiq_registry.bundle import Bundle
from fleetiq_worker import __main__ as runner
from fleetiq_worker.jobs import claim, finish
from fleetiq_worker.prediction_handler import (
    InferenceClient,
    compute,
    explain,
    prediction_input_hash,
)
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.integration


def test_native_prediction_atomic_replay_restart_and_outage(
    domain_connection, isolated_database, monkeypatch
):
    receipt_path = ROOT / "docs/exports/bundle_build.json"
    if not receipt_path.exists():
        pytest.skip("Build the approved native model bundle before acceptance testing")
    receipt = json.loads(receipt_path.read_text())
    settings = Settings().model_copy(
        update={
            "model_bundle_path": ROOT / receipt["path"],
            "model_bundle_sha256": receipt["approved_digest"],
        }
    )
    bundle = Bundle(settings.model_bundle_path, settings.model_bundle_sha256)
    c, ids = domain_connection
    at = datetime(2026, 1, 2, tzinfo=UTC)
    task = bundle.manifest["tasks"]["rul"]
    signature = dict(
        task="rul",
        track="cmapss_benchmark",
        unit="cycles",
        horizon=0,
        feature_version=task["feature_version"],
        model_version=task["version"],
        calibration_version="display_disabled",
        bundle_hash=bundle.hash,
    )
    deployment = c.scalar(
        sa.insert(ModelDeployment)
        .values(
            organization_id=ids["organization"],
            **signature,
            applicability={"scope": "benchmark_only"},
            effective_at=at - timedelta(days=1),
        )
        .returning(ModelDeployment.id)
    )
    frame = parse_trajectory(ROOT / "data/raw/cmapss/train_FD001.txt")
    frame = frame.loc[(frame.unit_id == 1) & (frame.cycle <= 30)]
    observations = [
        dict(
            subset="FD001",
            unit_id=1,
            cycle=int(row.cycle),
            settings=[float(row[f"setting_{i}"]) for i in range(1, 4)],
            sensors=[float(row[f"sensor_{i}"]) for i in range(1, 22)],
        )
        for _, row in frame.iterrows()
    ]
    inputs = dict(
        component_id=str(ids["component"]),
        deployment_id=str(deployment),
        task="rul",
        as_of=at.isoformat(),
        source_cutoff=at.isoformat(),
        observations=observations,
    )
    job = enqueue_job(
        c,
        dict(
            organization_id=ids["organization"],
            owner_id=ids["user"],
            kind="prediction.compute",
            input_hash=prediction_input_hash(inputs),
            input=inputs,
            idempotency_key="native-acceptance",
        ),
        actor_id=ids["user"],
        reason="Native observed-only fixture",
    )
    c.commit()
    migration = sa.create_engine(isolated_database[0])
    worker = sa.create_engine(
        make_url(settings.worker_database_url.get_secret_value()).set(database=isolated_database[1])
    )
    private = TestClient(
        create_app(
            folder=settings.model_bundle_path,
            approved_hash=bundle.hash,
            token=settings.inference_workload_token.get_secret_value(),
        )
    )
    private.__enter__()
    assert private.get("/health/ready").status_code == 200

    def serve(request):
        assert worker.pool.checkedout() == 0, "No worker SQL connection held during HTTP inference"
        response = private.post(
            "/internal/v1/predict",
            json=json.loads(request.content),
            headers={"Authorization": request.headers["Authorization"]},
        )
        return httpx.Response(response.status_code, json=response.json())

    client = InferenceClient(settings, transport=httpx.MockTransport(serve))
    try:
        with worker.begin() as tx:
            lease = claim(tx, "native-worker")
        outcome = compute(worker, lease, settings=settings, client=client)

        def broken(tx):
            outcome.effect(tx)
            raise RuntimeError("Simulated interruption before fenced completion")

        with pytest.raises(RuntimeError), worker.begin() as tx:
            finish(tx, lease, outcome.result, effect=broken)
        with migration.connect() as tx:
            assert tx.scalar(sa.select(sa.func.count()).select_from(Prediction)) == 0
            assert tx.scalar(sa.select(sa.func.count()).select_from(FeatureSnapshot)) == 0
            assert (
                tx.scalar(
                    sa.select(sa.func.count())
                    .select_from(Job)
                    .where(Job.kind == "prediction.explain")
                )
                == 0
            )
        with worker.begin() as tx:
            finish(tx, lease, outcome.result, effect=outcome.effect)
        # A new worker process can consume the queued explanation from immutable evidence.
        with worker.begin() as tx:
            explanation_lease = claim(tx, "restarted-worker")
        explanation = explain(worker, explanation_lease, settings=settings)
        assert explanation.result["status"] == "explained"
        assert explanation.result["explanation"]["scale"] == "cycles"
        with worker.begin() as tx:
            finish(tx, explanation_lease, explanation.result)
        with migration.begin() as tx:
            replay = enqueue_job(
                tx,
                dict(
                    organization_id=ids["organization"],
                    owner_id=ids["user"],
                    kind="prediction.compute",
                    input_hash=prediction_input_hash(inputs),
                    input=inputs,
                    idempotency_key="replay-new-worker",
                ),
                actor_id=ids["user"],
                reason="Replay identical observations",
            )
        with worker.begin() as tx:
            replay_lease = claim(tx, "restarted-worker")
        repeated = compute(worker, replay_lease, settings=settings, client=client)
        assert repeated.result["prediction_id"] == outcome.result["prediction_id"]
        with worker.begin() as tx:
            finish(tx, replay_lease, repeated.result, effect=repeated.effect)
        with migration.connect() as tx:
            assert tx.scalar(sa.select(sa.func.count()).select_from(Prediction)) == 1
            assert (
                tx.scalar(
                    sa.select(sa.func.count())
                    .select_from(Job)
                    .where(Job.kind == "prediction.explain")
                )
                == 1
            )
            assert (
                tx.scalar(
                    sa.select(sa.func.count())
                    .select_from(EventOutbox)
                    .where(EventOutbox.kind == "prediction.recorded")
                )
                == 1
            )
            assert tx.scalar(sa.select(Job.state).where(Job.id == replay)) == "completed"
        outage_inputs = inputs | dict(
            as_of=(at + timedelta(seconds=1)).isoformat(),
            source_cutoff=(at + timedelta(seconds=1)).isoformat(),
        )
        with migration.begin() as tx:
            outage_job = enqueue_job(
                tx,
                dict(
                    organization_id=ids["organization"],
                    owner_id=ids["user"],
                    kind="prediction.compute",
                    input_hash=prediction_input_hash(outage_inputs),
                    input=outage_inputs,
                    idempotency_key="outage",
                ),
                actor_id=ids["user"],
                reason="Service outage fixture",
            )

        def unavailable(request):
            return httpx.Response(503)

        outage_client = InferenceClient(settings, transport=httpx.MockTransport(unavailable))
        monkeypatch.setattr("fleetiq_worker.prediction_handler.Settings", lambda: settings)
        monkeypatch.setattr(
            "fleetiq_worker.prediction_handler.configured_client", lambda _: outage_client
        )
        assert runner.run(worker, once=True, concurrency=1) == {"retry_or_dead_letter": 1}
        with migration.connect() as tx:
            stored = tx.execute(sa.select(Prediction.__table__)).mappings().one()
            assert stored["output"]["remaining_cycles"] >= 0
            assert stored["quality"]["historical"] is True
            assert tx.scalar(sa.select(Job.state).where(Job.id == outage_job)) == "pending"
            assert tx.scalar(sa.select(Job.state).where(Job.id == job)) == "completed"
    finally:
        private.__exit__(None, None, None)
        worker.dispose()
        migration.dispose()
