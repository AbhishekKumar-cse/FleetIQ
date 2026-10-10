"""Bounded typed private inference; database configuration is deliberately absent."""

import os
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from fleetiq_registry.bundle import Bundle
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat


class FeatureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=128)
    task: Literal["failure_risk", "rul", "anomaly"]
    track: Literal["cmapss_benchmark", "synthetic_engine_demo", "synthetic_sensor_model"]
    unit: Literal["cycles", "score", "operating_hours"]
    horizon: int = Field(ge=0, le=1000)
    native_cycle: int = Field(ge=1, le=1000000)
    names: list[str] = Field(max_length=512)
    values: list[FiniteFloat] = Field(max_length=512)
    schema_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    supported: bool
    ood: bool = False


class PredictBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requests: list[FeatureRequest] = Field(min_length=1, max_length=32)


def create_app(*, folder=None, approved_hash=None, token=None, timeout=None, bundle_factory=Bundle):
    @asynccontextmanager
    async def lifespan(application):
        application.state.bundle = None
        application.state.token = token or os.getenv("INFERENCE_WORKLOAD_TOKEN", "")
        application.state.timeout = timeout or float(os.getenv("INFERENCE_TIMEOUT_SECONDS", "10"))
        application.state.executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="private-inference"
        )
        application.state.capacity = threading.BoundedSemaphore(2)
        try:
            if len(application.state.token) < 32 or not 0 < application.state.timeout <= 120:
                raise ValueError("Private inference configuration missing")
            application.state.bundle = bundle_factory(
                folder or os.getenv("MODEL_BUNDLE_PATH", ""),
                approved_hash or os.getenv("MODEL_BUNDLE_SHA256", ""),
            )
        except Exception as error:
            application.state.load_error = type(error).__name__
        yield
        application.state.executor.shutdown(wait=True, cancel_futures=True)

    app = FastAPI(
        title="FleetIQ private inference", lifespan=lifespan, docs_url=None, redoc_url=None
    )

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        if app.state.bundle is None:
            raise HTTPException(503, "Approved bundle unavailable")
        return dict(status="ready", bundle_hash=app.state.bundle.hash)

    @app.post("/internal/v1/predict")
    def predict(batch: PredictBatch, authorization: str | None = Header(default=None)):
        supplied = (
            authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
        )
        if not supplied or not secrets.compare_digest(supplied, app.state.token):
            raise HTTPException(401, "Workload authentication required")
        if app.state.bundle is None:
            raise HTTPException(503, "Approved bundle unavailable")
        if not app.state.capacity.acquire(blocking=False):
            raise HTTPException(503, "Inference capacity exhausted")

        def compute():
            return [app.state.bundle.predict(r.model_dump()) for r in batch.requests]

        try:
            future = app.state.executor.submit(compute)
        except Exception:
            app.state.capacity.release()
            raise
        future.add_done_callback(lambda _: app.state.capacity.release())
        try:
            return dict(
                bundle_hash=app.state.bundle.hash,
                predictions=future.result(timeout=app.state.timeout),
            )
        except TimeoutError:
            # Capacity remains held until actual work finishes, including after HTTP timeout.
            future.cancel()
            raise HTTPException(504, "Inference deadline exceeded") from None
        except ValueError:
            raise HTTPException(422, "Feature/task contract mismatch") from None

    return app
