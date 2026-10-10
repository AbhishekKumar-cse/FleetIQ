"""Observed-only benchmark jobs; inference outside SQL, evidence inside lease fencing."""

import json
import threading
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

import httpx
import numpy as np
import pandas as pd
import sqlalchemy as sa
from fleetiq_api.settings import Settings
from fleetiq_data.contracts import CmapssObservation
from fleetiq_domain.models.components import Component
from fleetiq_domain.models.operations import EventOutbox, enqueue_job
from fleetiq_domain.models.predictions import FeatureSnapshot, ModelDeployment, Prediction
from fleetiq_evaluation.rul import endpoint_vector
from fleetiq_evaluation.splits import content_hash
from fleetiq_features.__main__ import benchmark_inputs
from fleetiq_features.pipeline import PipelineFit
from fleetiq_registry.bundle import verify_files
from fleetiq_training.degradation_features import engine_features
from sqlalchemy.dialects.postgresql import insert as pg_insert

from fleetiq_worker.handlers import Outcome


class InferenceClient:
    """Bounded HTTP and a process-local breaker; durable retry belongs to job leases."""

    def __init__(self, settings, *, transport=None, clock=time.monotonic):
        self.settings, self.transport, self.clock = settings, transport, clock
        self.failures, self.open_until = 0, 0.0
        self.lock = threading.Lock()

    def predict(self, request):
        with self.lock:
            if self.clock() < self.open_until:
                raise RuntimeError("Inference circuit open")
        try:
            with httpx.Client(
                base_url=self.settings.inference_url,
                timeout=self.settings.inference_timeout_seconds,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                response = client.post(
                    "/internal/v1/predict",
                    headers={
                        "Authorization": "Bearer "
                        + self.settings.inference_workload_token.get_secret_value()
                    },
                    json={"requests": [request]},
                )
                response.raise_for_status()
                body = response.json()
            result = body["predictions"]
            if body["bundle_hash"] != self.settings.model_bundle_sha256 or len(result) != 1:
                raise ValueError("Unexpected inference bundle/batch")
            result = result[0]
            if any(
                result[key] != request[key]
                for key in ("request_id", "task", "track", "unit", "horizon")
            ):
                raise ValueError("Inference response identity mismatch")
            if result["bundle_hash"] != body["bundle_hash"]:
                raise ValueError("Prediction bundle mismatch")
        except Exception:
            with self.lock:
                self.failures += 1
                if self.failures >= 3:
                    self.open_until = self.clock() + 30
            raise
        with self.lock:
            self.failures, self.open_until = 0, 0
        return result


_clients = {}
_client_lock = threading.Lock()


def prediction_input_hash(inputs):
    """Canonical numeric identity survives PostgreSQL JSONB's 1.0 -> 1 normalization."""

    def canonical(value):
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, dict):
            return {k: canonical(v) for k, v in value.items()}
        if isinstance(value, list):
            return [canonical(v) for v in value]
        return value

    return content_hash(canonical(inputs))


def configured_client(settings):
    key = (settings.inference_url, settings.model_bundle_sha256)
    with _client_lock:
        if key not in _clients:
            _clients[key] = InferenceClient(settings)
        return _clients[key]


def prepare(inputs, folder, manifest, request_id):
    """Use the identical offline extraction on ingested native observations, never labels."""
    rows = inputs["observations"]
    if set(inputs) != {
        "observations",
        "task",
        "as_of",
        "source_cutoff",
        "component_id",
        "deployment_id",
    }:
        raise ValueError("Unexpected prediction inputs; outcomes/context are forbidden")
    if not 30 <= len(rows) <= 10000:
        raise ValueError("Bounded native observation history required")
    observed = [CmapssObservation.model_validate(row) for row in rows]
    task = inputs["task"]
    subsets = {r.subset for r in observed}
    expected = {"FD001"} if task == "rul" else {"FD003"}
    if task not in {"rul", "failure_risk"} or subsets != expected:
        raise ValueError("Task not validated for this benchmark subset")
    if len({r.unit_id for r in observed}) != 1:
        raise ValueError("One native engine per job required")
    frame = pd.DataFrame(
        [
            dict(
                unit_id=r.unit_id,
                cycle=r.cycle,
                **{f"setting_{i}": v for i, v in enumerate(r.settings, 1)},
                **{f"sensor_{i}": v for i, v in enumerate(r.sensors, 1)},
            )
            for r in observed
        ]
    ).sort_values("cycle")
    if not np.array_equal(frame.cycle, np.arange(1, len(frame) + 1)):
        raise ValueError("Contiguous observed history required")
    spec = manifest["tasks"][task]
    if task == "rul":
        fit = PipelineFit.load(folder / "rul/preprocessing.json")
        last = None
        for last in benchmark_inputs(frame):
            pass
        result = endpoint_vector(last, fit)
        names, vector, supported = (
            list(result["names"]),
            list(result["vector"]),
            result["supported"],
        )
    else:
        features = engine_features(frame).iloc[-1]
        names, vector, supported = list(features.index), features.tolist(), True
    if names != spec["names"]:
        raise ValueError("Feature extractor differs from approved bundle")
    return dict(
        request_id=request_id,
        task=task,
        track=spec["track"],
        unit=spec["unit"],
        horizon=spec["horizon"],
        native_cycle=len(frame),
        names=names,
        values=vector,
        schema_hash=spec["schema_hash"],
        supported=supported,
        ood=False,
    )


def compute(engine, lease, *, settings=None, client=None):
    settings = settings or Settings()
    job, org = lease.job, lease.organization_id
    inputs = job["input"]
    if prediction_input_hash(inputs) != job["input_hash"]:
        raise ValueError("Observed job input digest mismatch")
    folder = settings.model_bundle_path
    manifest = json.loads((folder / "manifest.json").read_text())
    verify_files(folder, manifest, settings.model_bundle_sha256)
    as_of, cutoff = (datetime.fromisoformat(inputs[key]) for key in ("as_of", "source_cutoff"))
    if any(d.tzinfo is None or d.utcoffset() != timedelta(0) for d in (as_of, cutoff)):
        raise ValueError("UTC administrative timestamps required")
    if as_of > cutoff or cutoff > datetime.now(UTC):
        raise ValueError("Future evidence cutoff forbidden")
    for observation in inputs["observations"]:
        received = observation.get("transport_received_at")
        if received and datetime.fromisoformat(received) > cutoff:
            raise ValueError("Observation received after source cutoff")
    component, deployment = UUID(inputs["component_id"]), UUID(inputs["deployment_id"])
    # Close the read connection before feature computation or network work.
    with engine.connect() as c:
        if not c.scalar(
            sa.select(Component.id).where(
                Component.id == component, Component.organization_id == org
            )
        ):
            raise ValueError("Component outside job organization")
        registered = dict(
            c.execute(
                sa.select(ModelDeployment.__table__).where(
                    ModelDeployment.id == deployment, ModelDeployment.organization_id == org
                )
            )
            .mappings()
            .one()
        )
    request = prepare(inputs, folder, manifest, str(uuid5(job["id"], "inference")))
    spec = manifest["tasks"][request["task"]]
    signature = dict(
        task=request["task"],
        track=request["track"],
        unit=request["unit"],
        horizon=request["horizon"],
        feature_version=spec["feature_version"],
        model_version=spec["version"],
        calibration_version="display_disabled",
        bundle_hash=settings.model_bundle_sha256,
    )
    if any(registered[k] != v for k, v in signature.items()) or registered["effective_at"] > as_of:
        raise ValueError("Deployment signature/effective time mismatch")
    if registered["applicability"].get("scope") != "benchmark_only":
        raise ValueError("Benchmark deployment requires explicit applicability")
    response = (client or configured_client(settings)).predict(request)
    json.dumps(response, allow_nan=False)
    if any(response[k] != v for k, v in signature.items()):
        raise ValueError("Returned model/feature version mismatch")
    if response["coverage"] not in {"full", "partial", "unsupported"}:
        raise ValueError("Invalid inference coverage")
    if not request["supported"] and response["coverage"] != "unsupported":
        raise ValueError("Inference ignored feature abstention")
    unsupported = response["coverage"] == "unsupported"
    if unsupported and response["output"] is not None:
        raise ValueError("Unsupported inference must not fabricate health")
    if not unsupported and not isinstance(response["output"], dict):
        raise ValueError("Supported inference requires typed output")
    feature_id = uuid5(
        component,
        content_hash(
            dict(
                as_of=as_of.isoformat(),
                input_hash=job["input_hash"],
                feature_version=spec["feature_version"],
            )
        ),
    )
    prediction_id = uuid5(feature_id, content_hash(signature))
    quality = dict(
        historical=True,
        scope="benchmark_only",
        native_cycle=request["native_cycle"],
        timestamps_are_transport_only=True,
        current_notifications=False,
        supported=not unsupported,
    )

    def persist(c):
        c.execute(
            pg_insert(FeatureSnapshot)
            .values(
                id=feature_id,
                organization_id=org,
                component_id=component,
                installation_id=None,
                as_of=as_of,
                window_start=as_of - timedelta(microseconds=1),
                window_end=as_of,
                source_cutoff=cutoff,
                feature_version=spec["feature_version"],
                input_hash=job["input_hash"],
                track=request["track"],
                vector=dict(zip(request["names"], request["values"], strict=True)),
                quality=quality,
            )
            .on_conflict_do_nothing()
        )
        inserted = c.scalar(
            pg_insert(Prediction)
            .values(
                id=prediction_id,
                organization_id=org,
                component_id=component,
                feature_snapshot_id=feature_id,
                deployment_id=deployment,
                as_of=as_of,
                source_cutoff=cutoff,
                input_hash=job["input_hash"],
                **signature,
                quality=quality,
                ood=False,
                coverage=response["coverage"],
                output=response["output"],
                uncertainty=response["uncertainty"],
                explanation_status="unsupported" if unsupported else "pending",
            )
            .on_conflict_do_nothing()
            .returning(Prediction.id)
        )
        if inserted is None:
            return
        if not unsupported:
            explanation_input = dict(
                prediction_id=str(prediction_id), bundle_hash=settings.model_bundle_sha256
            )
            enqueue_job(
                c,
                dict(
                    organization_id=org,
                    owner_id=job["owner_id"],
                    kind="prediction.explain",
                    input_hash=content_hash(explanation_input),
                    input=explanation_input,
                    idempotency_key="explain:" + str(prediction_id),
                ),
                actor_id=job["owner_id"],
                reason="Frozen prediction explanation",
            )
        c.execute(
            sa.insert(EventOutbox).values(
                organization_id=org,
                scope_kind="owner",
                scope_id=job["owner_id"],
                kind="prediction.recorded",
                payload=dict(
                    prediction_id=str(prediction_id), historical=True, coverage=response["coverage"]
                ),
            )
        )

    return Outcome(
        dict(
            status="unsupported" if unsupported else "predicted",
            prediction_id=str(prediction_id),
            historical=True,
        ),
        unsupported,
        persist,
    )


def explain(engine, lease, *, settings=None):
    settings = settings or Settings()
    job = lease.job
    if (
        content_hash(job["input"]) != job["input_hash"]
        or job["input"]["bundle_hash"] != settings.model_bundle_sha256
    ):
        raise ValueError("Explanation job version mismatch")
    with engine.connect() as c:
        prediction = dict(
            c.execute(
                sa.select(Prediction.__table__).where(
                    Prediction.id == UUID(job["input"]["prediction_id"]),
                    Prediction.organization_id == lease.organization_id,
                )
            )
            .mappings()
            .one()
        )
        feature = dict(
            c.execute(
                sa.select(FeatureSnapshot.__table__).where(
                    FeatureSnapshot.id == prediction["feature_snapshot_id"],
                    FeatureSnapshot.organization_id == lease.organization_id,
                )
            )
            .mappings()
            .one()
        )
    if prediction["bundle_hash"] != settings.model_bundle_sha256:
        raise ValueError("Explanation cannot cross model versions")
    if prediction["task"] != "rul":
        return Outcome(
            dict(
                status="unsupported",
                prediction_id=str(prediction["id"]),
                reason="Aggregate soft-voting probability attribution unsupported",
            ),
            True,
        )
    from fleetiq_domain.explanations import tree_explanation
    from fleetiq_registry.bundle import Bundle
    from xgboost import XGBRegressor

    bundle = Bundle(settings.model_bundle_path, settings.model_bundle_sha256)
    names = bundle.manifest["tasks"]["rul"]["names"]
    model = XGBRegressor(n_jobs=2)
    model.load_model(bundle.folder / "rul/regressor.json")
    background = json.loads((bundle.folder / "rul/background.json").read_text())["values"]
    result = tree_explanation(
        model,
        [[feature["vector"][n] for n in names]],
        names,
        background,
        scale="cycles",
        model_hash=bundle.hash,
        feature_version=prediction["feature_version"],
    )
    if not np.isclose(result["raw_outputs"][0], prediction["output"]["raw_cycles"], atol=2e-5):
        raise ValueError("Explanation does not reconstruct stored prediction")
    return Outcome(
        dict(status="explained", prediction_id=str(prediction["id"]), explanation=result)
    )
