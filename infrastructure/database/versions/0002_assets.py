"""Scoped organization, site, fleet, type and aircraft identities."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_assets"
down_revision = "0001_extensions"
branch_labels = None
depends_on = None


def identity_columns():
    return [
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    ]


def scope_constraints(table):
    return [
        sa.PrimaryKeyConstraint("id", name=f"pk_{table}"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=f"fk_{table}_organization_id_organization",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("organization_id", "id", name=f"uq_{table}_org_id"),
    ]


def upgrade():
    op.create_table(
        "organization",
        *identity_columns(),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_organization"),
        sa.UniqueConstraint("code", name="uq_organization_code"),
    )
    for table in ("site", "fleet", "aircraft_type"):
        columns = [
            *identity_columns(),
            sa.Column("organization_id", sa.Uuid(), nullable=False),
            sa.Column("code", sa.String(64), nullable=False),
        ]
        if table == "aircraft_type":
            columns.append(
                sa.Column(
                    "configuration",
                    postgresql.JSONB(),
                    nullable=False,
                    server_default=sa.text("'{}'::jsonb"),
                )
            )
        else:
            columns.append(sa.Column("name", sa.String(200), nullable=False))
        op.create_table(
            table,
            *columns,
            *scope_constraints(table),
            sa.UniqueConstraint("organization_id", "code", name=f"uq_{table}_org_code"),
        )
    op.create_table(
        "aircraft",
        *identity_columns(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("tail_label", sa.String(64), nullable=False),
        sa.Column("type_id", sa.Uuid(), nullable=False),
        sa.Column("fleet_id", sa.Uuid(), nullable=False),
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("status_version", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        *scope_constraints("aircraft"),
        sa.UniqueConstraint("organization_id", "tail_label", name="uq_aircraft_org_tail"),
        sa.CheckConstraint("status_version >= 0", name=op.f("ck_aircraft_status_version")),
        *[
            sa.ForeignKeyConstraint(
                ["organization_id", f"{field}_id"],
                [f"{table}.organization_id", f"{table}.id"],
                name=f"fk_aircraft_org_{field}",
                ondelete="RESTRICT",
            )
            for field, table in (("type", "aircraft_type"), ("fleet", "fleet"), ("site", "site"))
        ],
    )
    for field in ("type", "fleet", "site"):
        op.create_index(f"ix_aircraft_org_{field}", "aircraft", ["organization_id", f"{field}_id"])
    # No mutating API exists yet: grant only the read access needed at this schema stage.
    op.execute(
        "GRANT SELECT ON organization, site, fleet, aircraft_type, aircraft TO fleetiq_app, fleetiq_worker"
    )


def downgrade():
    for field in ("type", "fleet", "site"):
        op.drop_index(f"ix_aircraft_org_{field}", table_name="aircraft")
    for table in ("aircraft", "aircraft_type", "fleet", "site", "organization"):
        op.drop_table(table)
