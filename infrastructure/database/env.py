"""Online migrations use typed credentials and protect operational downgrade paths."""

from alembic import context
from fleetiq_domain.db import Base, guard_test_url
from fleetiq_domain.models import assets  # noqa: F401 - register ORM metadata
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

config = context.config
url = config.attributes.get("url")
expected = config.attributes.get("expected_test_database")
operation = config.attributes.get("operation", "upgrade")
cli = getattr(config.cmd_opts, "cmd", None)
if cli:
    operation = cli[0].__name__
if url is None:
    from fleetiq_api.settings import Settings

    url = make_url(Settings().migration_database_url.get_secret_value())
    expected = context.get_x_argument(as_dictionary=True).get("test_database")
    if expected:
        url = url.set(database=expected)
if expected:
    guard_test_url(url, expected)
if operation in {"downgrade", "stamp"}:
    if not expected:
        raise ValueError("operational downgrade/stamp is forbidden; use the isolated test harness")
    guard_test_url(url, expected)
if context.is_offline_mode():
    raise ValueError("online guarded migrations are required")
engine = create_engine(url, poolclass=NullPool)
try:
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
finally:
    engine.dispose()
