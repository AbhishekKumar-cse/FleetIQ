"""Preserve generated local credentials while wiring an isolated Docker database."""

import argparse
import json
import os
import secrets
import shlex
from pathlib import Path

from create_dev_secrets import ROOT, bootstrap, private_write
from sqlalchemy.engine import URL, make_url


def configure_database(root: Path = ROOT, port: int = 5433) -> None:
    if not 1024 <= port <= 65535:
        raise ValueError("invalid local port")
    bootstrap(root)
    env = (root / ".env").resolve()
    values = dict(
        shlex.split(line)[0].split("=", 1) for line in env.read_text().splitlines() if line
    )
    roles = {}
    for field in (
        "DATABASE_URL",
        "WORKER_DATABASE_URL",
        "MIGRATION_DATABASE_URL",
        "TEST_ADMIN_DATABASE_URL",
        "MLFLOW_DATABASE_URL",
    ):
        url = make_url(values[field])
        if url.host != "127.0.0.1" or url.database not in {"fleetiq", "postgres"}:
            raise ValueError("only generated loopback development URLs may be configured")
        roles[url.username] = url.password
        values[field] = url.set(
            port=port, database="postgres" if field == "TEST_ADMIN_DATABASE_URL" else "fleetiq"
        ).render_as_string(hide_password=False)
    if set(roles) != {
        f"fleetiq_{name}" for name in ("app", "worker", "migration", "test_admin", "mlflow")
    }:
        raise ValueError("unexpected development database roles")
    secret_dir = env.parent
    admin = secret_dir / "db_admin_password"
    private_write(admin, secrets.token_urlsafe(48).encode())
    role_file = secret_dir / "db_roles.json"
    role_bytes = (json.dumps(roles, sort_keys=True) + "\n").encode()
    private_write(role_file, role_bytes)
    if role_file.read_bytes() != role_bytes:
        raise ValueError("existing database role credentials differ; preserve and reconcile")
    values |= {
        "DB_PORT": str(port),
        "DB_ADMIN_PASSWORD_FILE": str(admin),
        "DB_ROLES_FILE": str(role_file),
        "DB_BOOTSTRAP_ADMIN_URL": URL.create(
            "postgresql+psycopg",
            username="postgres",
            password=admin.read_text(),
            host="127.0.0.1",
            port=port,
            database="postgres",
        ).render_as_string(hide_password=False),
    }
    updated = "".join(f"{key}={shlex.quote(value)}\n" for key, value in values.items()).encode()
    if env.read_bytes() != updated:
        private_write(secret_dir / "dev.env.before-database", env.read_bytes())
        temporary = secret_dir / "dev.env.database-update"
        with temporary.open("xb") as handle:
            os.chmod(temporary, 0o600)
            handle.write(updated)
        temporary.replace(env)
    print(
        "Isolated database credentials ready; existing role/auth secrets preserved; no secrets printed."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=5433)
    arguments = parser.parse_args()
    configure_database(port=arguments.port)
