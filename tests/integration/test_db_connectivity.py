import pytest
from fleetiq_api.settings import Settings
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration


def test_postgresql17_timescale_and_local_role_boundaries():
    settings = Settings()
    for field in (
        "database_url",
        "worker_database_url",
        "migration_database_url",
        "test_admin_database_url",
        "mlflow_database_url",
    ):
        engine = create_engine(getattr(settings, field).get_secret_value())
        try:
            with engine.connect() as connection:
                assert connection.scalar(text("SELECT 1")) == 1
                major = int(connection.scalar(text("SHOW server_version_num"))) // 10000
                assert major == 17
                flags = connection.execute(
                    text("SELECT rolsuper, rolcreaterole FROM pg_roles WHERE rolname=current_user")
                ).one()
                assert flags == (False, False)
                if field != "test_admin_database_url":
                    assert connection.scalar(
                        text("SELECT extversion FROM pg_extension WHERE extname='timescaledb'")
                    )
                if field in {"database_url", "worker_database_url"}:
                    assert not connection.scalar(
                        text("SELECT has_schema_privilege(current_user,'public','CREATE')")
                    )
                    assert not connection.scalar(
                        text(
                            "SELECT has_database_privilege(current_user,current_database(),'CREATE')"
                        )
                    )
        finally:
            engine.dispose()
