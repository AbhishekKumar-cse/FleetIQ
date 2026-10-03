import json
import logging
import runpy
from pathlib import Path

import pytest
from fleetiq_api.logging import JsonFormatter
from fleetiq_api.settings import Settings
from pydantic import ValidationError

BOOTSTRAP = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "scripts/create_dev_secrets.py")
)["bootstrap"]


@pytest.fixture
def configured(tmp_path, monkeypatch):
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    root = tmp_path / "project"
    env = BOOTSTRAP(root, tmp_path / "private")
    return root, env


def test_secrets_are_private_and_idempotent(configured, tmp_path):
    root, env = configured
    before = env.read_bytes()
    key = (root / ".secrets/auth_private.pem").read_bytes()
    BOOTSTRAP(root, tmp_path / "private")
    assert env.read_bytes() == before
    assert (root / ".secrets/auth_private.pem").read_bytes() == key
    assert env.stat().st_mode & 0o077 == 0
    assert (root / ".secrets/auth_private.pem").stat().st_mode & 0o077 == 0
    settings = Settings(_env_file=env, project_root=root)
    assert settings.worker_concurrency == 1
    assert settings.report_root == root / "docs/exports"


def test_missing_configuration_is_rejected(monkeypatch):
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    "override",
    [
        {"worker_concurrency": 0},
        {"allowed_origins": ["*"]},
        {"source_track": "unknown"},
        {"report_root": "reports"},
    ],
)
def test_invalid_settings_are_rejected(configured, override):
    root, env = configured
    with pytest.raises(ValidationError):
        Settings(_env_file=env, project_root=root, **override)


def test_messages_nested_context_and_exceptions_are_redacted():
    secret = "private-token-value"
    record = logging.LogRecord(
        "fleetiq",
        logging.ERROR,
        __file__,
        1,
        "token=%s postgresql://user:password@host/db",
        (secret,),
        None,
    )
    record.request_id = "request-1"
    record.job_id = "job-1"
    record.context = {"authorization": secret, "nested": {"safe": secret}}
    payload = JsonFormatter((secret,)).format(record)
    assert secret not in payload and "user:password" not in payload
    data = json.loads(payload)
    assert data["request_id"] == "request-1" and data["job_id"] == "job-1"
    assert data["context"]["authorization"] == "[REDACTED]"
