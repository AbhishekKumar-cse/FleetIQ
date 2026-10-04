"""Guarded disposable database context shared by integration tests and local benchmarks."""

from contextlib import contextmanager
from uuid import uuid4

from alembic import command
from fleetiq_api.settings import Settings
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from fleetiq_domain.db import guard_test_url
from fleetiq_domain.migrations import migration_config


@contextmanager
def temporary_database(settings=None):
    settings = settings or Settings()
    name = f"fleetiq_test_{uuid4().hex}"
    admin = make_url(settings.test_admin_database_url.get_secret_value())
    url = make_url(settings.migration_database_url.get_secret_value()).set(database=name)
    bootstrap = make_url(settings.db_bootstrap_admin_url.get_secret_value()).set(database=name)
    guard_test_url(url, name)
    guard_test_url(bootstrap, name)
    if admin.database != "postgres" or (admin.host, admin.port) != (url.host, url.port):
        raise ValueError("test admin must use the same loopback server control database")
    admin_engine = create_engine(admin, isolation_level="AUTOCOMMIT", hide_parameters=True)
    created = False
    try:
        with admin_engine.connect() as c:
            c.exec_driver_sql(
                f'CREATE DATABASE "{name}" OWNER fleetiq_migration TEMPLATE template0'
            )
            created = True
        engine = create_engine(bootstrap, hide_parameters=True)
        try:
            with engine.begin() as c:
                c.exec_driver_sql("CREATE EXTENSION timescaledb")
                c.exec_driver_sql("CREATE EXTENSION btree_gist")
        finally:
            engine.dispose()
        command.upgrade(migration_config(url, expected_test_database=name), "head")
        yield url, name
    finally:
        try:
            if created:
                guard_test_url(url, name)
                guard_test_url(bootstrap, name)
                engine = create_engine(
                    bootstrap.set(database="postgres"),
                    isolation_level="AUTOCOMMIT",
                    hide_parameters=True,
                )
                try:
                    with engine.connect() as c:
                        c.exec_driver_sql(f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS false')
                        c.execute(
                            text(
                                "SELECT pg_terminate_backend(pid,5000) FROM pg_stat_activity "
                                "WHERE datname=:name AND pid<>pg_backend_pid()"
                            ),
                            {"name": name},
                        ).all()
                finally:
                    engine.dispose()
                with admin_engine.connect() as c:
                    c.exec_driver_sql(f'DROP DATABASE "{name}"')
        finally:
            admin_engine.dispose()
