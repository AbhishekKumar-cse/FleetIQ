"""Verify installed package boundaries and the initial API contract."""

import importlib
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fleetiq_api.main import app

PACKAGES = (
    "fleetiq_api",
    "fleetiq_worker",
    "fleetiq_inference",
    "fleetiq_domain",
    "fleetiq_data",
    "fleetiq_features",
    "fleetiq_scheduling",
    "fleetiq_simulation",
    "fleetiq_training",
    "fleetiq_evaluation",
    "fleetiq_registry",
)


@pytest.mark.parametrize("package", PACKAGES)
def test_all_packages_import(package: str) -> None:
    module = importlib.import_module(package)
    assert module.__file__ is not None


def test_imports_work_outside_checkout_without_pythonpath(tmp_path: Path) -> None:
    # -I ignores PYTHONPATH, the current directory and user site packages.
    result = subprocess.run(
        [sys.executable, "-I", "-c", "import " + ", ".join(PACKAGES)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_health_live_contract() -> None:
    with TestClient(app) as client:
        response = client.get("/health/live")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"status": "ok"}


def test_health_endpoint_is_read_only() -> None:
    with TestClient(app) as client:
        response = client.post("/health/live")
    assert response.status_code == 405


def test_liveness_does_not_claim_dependency_readiness() -> None:
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 404


def test_openapi_identifies_initial_api() -> None:
    schema = app.openapi()
    assert schema["info"] == {"title": "FleetIQ", "version": "0.1.0"}
    assert "/health/live" in schema["paths"]
