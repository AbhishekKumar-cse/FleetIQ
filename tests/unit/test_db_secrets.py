import runpy
from pathlib import Path

import pytest
from fleetiq_api.settings import Settings
from sqlalchemy.engine import make_url


def test_private_database_configuration_preserves_credentials_and_repeats(tmp_path, monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    bootstrap = runpy.run_path(str(scripts / "create_dev_secrets.py"))["bootstrap"]
    configure = runpy.run_path(str(scripts / "create_db_secrets.py"))["configure_database"]
    root = tmp_path / "project"
    bootstrap(root)
    original = Settings(_env_file=root / ".env", project_root=root)
    original_password = make_url(original.database_url.get_secret_value()).password
    original_key = (root / ".secrets/auth_private.pem").read_bytes()
    configure(root, 5433)
    first = (root / ".env").read_bytes()
    configure(root, 5433)
    assert first == (root / ".env").read_bytes()
    assert original_key == (root / ".secrets/auth_private.pem").read_bytes()
    settings = Settings(_env_file=root / ".env", project_root=root)
    url = make_url(settings.database_url.get_secret_value())
    assert url.port == 5433 and url.password == original_password
    assert make_url(settings.test_admin_database_url.get_secret_value()).database == "postgres"
    for path in (settings.db_roles_file, settings.db_admin_password_file, root / ".env"):
        assert path.stat().st_mode & 0o077 == 0
    assert (root / ".secrets/dev.env.before-database").exists()


def test_invalid_database_port_rejected_before_mutation(tmp_path, monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    configure = runpy.run_path(str(scripts / "create_db_secrets.py"))["configure_database"]
    with pytest.raises(ValueError):
        configure(tmp_path / "unused", 1)
    assert not (tmp_path / "unused").exists()
