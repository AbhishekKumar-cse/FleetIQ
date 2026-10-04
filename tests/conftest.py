"""Disposable integration databases are named and guarded before creation/cleanup."""

from uuid import uuid4

import pytest
from alembic import command
from fleetiq_api.settings import Settings
from fleetiq_domain.db import guard_test_url
from fleetiq_domain.migrations import migration_config


@pytest.fixture
def domain_connection(isolated_database):
    from fleetiq_domain.models.assets import Organization, Site, Fleet, AircraftType, Aircraft
    from fleetiq_domain.models.components import Component, Installation
    from datetime import UTC, datetime
    from sqlalchemy import insert
    engine=create_engine(isolated_database[0])
    try:
        with engine.begin() as connection:
            ids={}
            ids['organization']=connection.scalar(insert(Organization).values(code='DEMO',name='Demo').returning(Organization.id))
            for key,model in (('site',Site),('fleet',Fleet),('type',AircraftType)):
                values=dict(organization_id=ids['organization'],code='DEMO')
                if model!=AircraftType:
                    values['name']='Demo'
                ids[key]=connection.scalar(insert(model).values(**values).returning(model.id))
            ids['aircraft']=connection.scalar(insert(Aircraft).values(organization_id=ids['organization'],tail_label='DEMO',type_id=ids['type'],site_id=ids['site'],fleet_id=ids['fleet']).returning(Aircraft.id))
            ids['component']=connection.scalar(insert(Component).values(organization_id=ids['organization'],serial='DEMO',kind='engine').returning(Component.id))
            ids['installation']=connection.scalar(insert(Installation).values(organization_id=ids['organization'],aircraft_id=ids['aircraft'],component_id=ids['component'],position='engine',installed_at=datetime(2026,1,1,tzinfo=UTC)).returning(Installation.id))
            yield connection,ids
    finally:
        engine.dispose()
from sqlalchemy import create_engine, text
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
        try:
            if created:
                guard_test_url(url, name)
                guard_test_url(bootstrap, name)
                # Stop asynchronous worker reconnection before guarded, database-scoped termination.
                cleanup_engine = create_engine(
                    bootstrap.set(database="postgres"), isolation_level="AUTOCOMMIT"
                )
                try:
                    with cleanup_engine.connect() as connection:
                        connection.exec_driver_sql(
                            f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS false'
                        )
                        connection.execute(
                            text(
                                "SELECT pg_terminate_backend(pid, 5000) FROM pg_stat_activity "
                                "WHERE datname=:name AND pid<>pg_backend_pid()"
                            ),
                            {"name": name},
                        ).all()
                finally:
                    cleanup_engine.dispose()
                with admin_engine.connect() as connection:
                    connection.exec_driver_sql(f'DROP DATABASE "{name}"')
        finally:
            admin_engine.dispose()
