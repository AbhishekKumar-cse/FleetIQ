"""Disposable integration databases are named and guarded before creation/cleanup."""

from uuid import uuid4

import pytest
from alembic import command
from fleetiq_api.settings import Settings
from fleetiq_domain.db import guard_test_url
from fleetiq_domain.migrations import migration_config
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


@pytest.fixture
def isolated_database():
    settings = Settings()
    name = f"fleetiq_test_{uuid4().hex}"
    admin = make_url(settings.test_admin_database_url.get_secret_value())
    url = make_url(settings.migration_database_url.get_secret_value()).set(database=name)
    bootstrap = make_url(settings.db_bootstrap_admin_url.get_secret_value()).set(database=name)
    guard_test_url(url, name)
    guard_test_url(bootstrap, name)
    if admin.database != "postgres" or admin.host != url.host or admin.port != url.port:
        raise ValueError(
            "test admin must target the same loopback server's postgres control database"
        )
    admin_engine = create_engine(admin, isolation_level="AUTOCOMMIT")
    created = False
    try:
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(
                f'CREATE DATABASE "{name}" OWNER fleetiq_migration TEMPLATE template0'
            )
            created = True
        extension_engine = create_engine(bootstrap)
        try:
            with extension_engine.begin() as connection:
                connection.exec_driver_sql("CREATE EXTENSION timescaledb")
                connection.exec_driver_sql("CREATE EXTENSION btree_gist")
        finally:
            extension_engine.dispose()
        command.upgrade(migration_config(url, expected_test_database=name), "head")
        yield url, name
    finally:
        if created:
            guard_test_url(url, name)
            with admin_engine.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin_engine.dispose()
