"""Initial API process liveness; dependency readiness comes in later steps."""

from fastapi import FastAPI

app = FastAPI(title="FleetIQ", version="0.1.0")


@app.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}
