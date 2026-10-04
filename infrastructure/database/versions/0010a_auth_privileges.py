"""Limit runtime session mutations to rotation, activity and revocation fields."""

from alembic import op

revision = "0010a_auth_privileges"
down_revision = "0010_operations"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("REVOKE UPDATE ON auth_session,auth_rate_limit FROM fleetiq_app")
    op.execute(
        "GRANT UPDATE (refresh_hash,csrf_hash,last_active_at,revoked,revoked_at,rotation) ON auth_session TO fleetiq_app"
    )
    op.execute("GRANT UPDATE (window_start,attempts) ON auth_rate_limit TO fleetiq_app")


def downgrade():
    op.execute(
        "REVOKE UPDATE (refresh_hash,csrf_hash,last_active_at,revoked,revoked_at,rotation) ON auth_session FROM fleetiq_app"
    )
    op.execute("REVOKE UPDATE (window_start,attempts) ON auth_rate_limit FROM fleetiq_app")
    op.execute("GRANT UPDATE ON auth_session,auth_rate_limit TO fleetiq_app")
