"""Establish extension prerequisites without elevating runtime roles."""

from alembic import op

revision = "0001_extensions"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # TimescaleDB is preinstalled by the privileged bootstrap on operational/test databases.
    # A missing nontrusted extension fails here rather than granting migration superuser.
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA public TO fleetiq_app, fleetiq_worker")


def downgrade():
    # Retain bootstrap-owned shared extensions; an isolated database is dropped by its fixture.
    pass
