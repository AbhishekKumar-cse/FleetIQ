"""Trusted local operator provisioning; source secrets are never printed."""

import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
from fleetiq_api.auth import digest
from fleetiq_api.settings import Settings
from fleetiq_data.seed import demo_id, grant_demo_import
from fleetiq_domain.models.ingestion import SourceAccess


def main():
    path = Path(".secrets/replay_source.json")
    if path.exists():
        old = json.loads(path.read_text())
        key, secret = old["token"].split(":")
        key = UUID(key)
    else:
        key, secret = uuid4(), secrets.token_urlsafe(48)
    engine = sa.create_engine(
        Settings().migration_database_url.get_secret_value(), hide_parameters=True
    )
    try:
        with engine.begin() as c:
            grant_demo_import(c)
            row = dict(
                id=key,
                organization_id=demo_id("organization"),
                source_id=demo_id("source"),
                user_id=demo_id("user"),
                token_hash=digest(secret),
                expires_at=datetime.now(UTC) + timedelta(days=30),
                revoked=False,
            )
            if c.scalar(sa.select(SourceAccess.id).where(SourceAccess.id == key)):
                c.execute(
                    sa.update(SourceAccess)
                    .where(SourceAccess.id == key)
                    .values(expires_at=row["expires_at"], revoked=False)
                )
            else:
                c.execute(sa.insert(SourceAccess).values(**row))
        path.write_text(
            json.dumps({"source_id": str(demo_id("source")), "token": str(key) + ":" + secret})
        )
        os.chmod(path, 0o600)
        print("Local source credential provisioned; private file only.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
