"""Migration configuration keeps URLs out of ini files and downgrade off operational storage."""

import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import URL, make_url

from fleetiq_domain.db import guard_test_url

ROOT = Path(__file__).resolve().parents[3]


def migration_config(
    url: str | URL, *, operation: str = "upgrade", expected_test_database: str | None = None
) -> Config:
    url = make_url(url)
    if operation not in {"upgrade", "current", "downgrade"}:
        raise ValueError("unsupported migration operation")
    if expected_test_database is not None:
        guard_test_url(url, expected_test_database)
    if operation == "downgrade" and expected_test_database is None:
        raise ValueError("downgrade is restricted to a named isolated test database")
    config = Config(str(ROOT / "infrastructure/database/alembic.ini"))
    config.attributes.update(
        url=url, operation=operation, expected_test_database=expected_test_database
    )
    return config


def main():
    from fleetiq_api.settings import Settings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["upgrade", "current", "downgrade"])
    parser.add_argument("revision", nargs="?")
    parser.add_argument("--test-database")
    args = parser.parse_args()
    settings = Settings()
    url = make_url(settings.migration_database_url.get_secret_value())
    if args.test_database:
        url = url.set(database=args.test_database)
    try:
        config = migration_config(
            url, operation=args.operation, expected_test_database=args.test_database
        )
    except ValueError as error:
        parser.error(str(error))
    if args.operation == "upgrade":
        command.upgrade(config, args.revision or "head")
    elif args.operation == "downgrade":
        if args.revision is None:
            parser.error("downgrade requires an explicit test revision")
        command.downgrade(config, args.revision)
    else:
        command.current(config)


if __name__ == "__main__":
    main()
