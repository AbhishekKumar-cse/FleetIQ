import pytest
from alembic import command
from fleetiq_domain.migrations import migration_config
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration


def test_isolated_upgrade_downgrade_upgrade_and_extensions(isolated_database):
    url, name = isolated_database
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            extensions = set(connection.scalars(text("SELECT extname FROM pg_extension")))
            assert {"timescaledb", "btree_gist"} <= extensions
            assert connection.scalar(text("SELECT current_database()")) == name
            assert connection.scalar(text("SELECT version_num FROM alembic_version"))
        command.downgrade(
            migration_config(url, operation="downgrade", expected_test_database=name), "base"
        )
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM alembic_version")) == 0
        command.upgrade(migration_config(url, expected_test_database=name), "head")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version"))
    finally:
        engine.dispose()
