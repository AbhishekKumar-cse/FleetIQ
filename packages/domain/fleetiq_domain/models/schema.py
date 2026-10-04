"""Shared declarations; migrations store frozen compiled DDL, never live model imports."""

from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from fleetiq_domain.db import Base

TYPES = {
    "uuid": sa.Uuid,
    "text": sa.Text,
    "time": lambda: sa.DateTime(timezone=True),
    "date": sa.Date,
    "number": lambda: sa.Numeric(18, 6),
    "int": sa.BigInteger,
    "float": sa.Double,
    "bool": sa.Boolean,
    "json": JSONB,
}


def entity(name, fields, *, refs=None, checks=(), unique=(), primary=None):
    columns = []
    if primary is None:
        columns.append(
            sa.Column(
                "id",
                sa.Uuid(),
                primary_key=True,
                default=uuid4,
                server_default=sa.text("gen_random_uuid()"),
            )
        )
    columns += [
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    ]
    for field, spec in fields.items():
        kind, nullable, *default = spec.split(":")
        columns.append(
            sa.Column(
                field,
                TYPES[kind](),
                nullable=nullable == "optional",
                server_default=sa.text(default[0]) if default else None,
            )
        )
    constraints = [
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"], ondelete="RESTRICT")
    ]
    if primary is None:
        constraints.append(sa.UniqueConstraint("organization_id", "id", name=f"uq_{name}_org_id"))
    else:
        constraints.append(sa.PrimaryKeyConstraint(*primary))
    for field, target in (refs or {}).items():
        constraints.append(
            sa.ForeignKeyConstraint(
                ["organization_id", field],
                [f"{target}.organization_id", f"{target}.id"],
                name=f"fk_{name}_{field}",
                ondelete="RESTRICT",
            )
        )
    constraints += [sa.CheckConstraint(value, name=f"rule_{i}") for i, value in enumerate(checks)]
    constraints += [
        sa.UniqueConstraint("organization_id", *keys, name=f"uq_{name}_key_{i}")
        for i, keys in enumerate(unique)
    ]
    table = sa.Table(name, Base.metadata, *columns, *constraints)
    model = type("".join(part.title() for part in name.split("_")), (), {})
    Base.registry.map_imperatively(model, table)
    return model
