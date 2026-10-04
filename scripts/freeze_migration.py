"""Freeze reviewed model tables into a migration; no runtime dependency on mutable models."""

import argparse
import importlib
from pathlib import Path

from fleetiq_domain.db import Base
from fleetiq_domain.models import assets  # noqa: F401
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

ROOT = Path(__file__).resolve().parents[1]


def freeze(module, revision, parent, extra):
    order = [
        "components",
        "telemetry",
        "history",
        "work",
        "inventory",
        "predictions",
        "fleet",
        "operations",
    ]
    for dependency in order[: order.index(module)]:
        importlib.import_module(f"fleetiq_domain.models.{dependency}")
    before = set(Base.metadata.tables)
    imported = importlib.import_module(f"fleetiq_domain.models.{module}")
    names = set(Base.metadata.tables) - before
    tables = [table for table in Base.metadata.sorted_tables if table.name in names]
    statements = [str(CreateTable(table).compile(dialect=postgresql.dialect())) for table in tables]
    statements += [
        str(CreateIndex(index).compile(dialect=postgresql.dialect()))
        for table in tables
        for index in sorted(table.indexes, key=lambda i: i.name)
    ]
    statements += getattr(imported, extra, []) if extra else []
    statements += [
        f"GRANT SELECT ON {','.join(table.name for table in tables)} TO fleetiq_app, fleetiq_worker"
    ]
    target = ROOT / f"infrastructure/database/versions/{revision}.py"
    content = f'"""Frozen {module} schema."""\nfrom alembic import op\nrevision={revision!r}\ndown_revision={parent!r}\nbranch_labels=None\ndepends_on=None\n\ndef upgrade():\n'
    content += "".join(f"    op.execute({statement!r})\n" for statement in statements)
    content += (
        "\ndef downgrade():\n"
        + "".join(
            f"    op.execute({statement!r})\n" for statement in getattr(imported, "DOWN_EXTRA", [])
        )
        + "".join(f'    op.execute("DROP TABLE {table.name}")\n' for table in reversed(tables))
    )
    with target.open("x") as handle:
        handle.write(content)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("module", "revision", "parent"):
        parser.add_argument(name)
    parser.add_argument("--extra", default="")
    args = parser.parse_args()
    freeze(args.module, args.revision, args.parent, args.extra)
