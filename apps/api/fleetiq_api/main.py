"""Initial API process liveness; dependency readiness comes in later steps."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from fleetiq_api.routers.auth import router
from fleetiq_api.routers.ingestion import router as ingestion_router
from fleetiq_api.settings import Settings


def create_app(auth_service=None):
    @asynccontextmanager
    async def lifespan(application):
        yield
        if application.state.auth_service is not None:
            application.state.auth_service.engine.dispose()

    application = FastAPI(title="FleetIQ", version="0.1.0", lifespan=lifespan)
    application.state.auth_service = auth_service
    try:
        origins = (
            auth_service.settings.allowed_origins if auth_service else Settings().allowed_origins
        )
    except ValidationError:
        # Liveness remains available without configuration; auth resolves strict settings on use.
        origins = []
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-CSRF-Token", "Authorization", "Idempotency-Key"],
    )
    application.include_router(router)
    application.include_router(ingestion_router)
    application.add_api_route("/health/live", live, methods=["GET"])
    return application


def live() -> dict[str, str]:
    return {"status": "ok"}


app = create_app()
