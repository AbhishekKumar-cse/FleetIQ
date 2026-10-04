"""Shared ORM metadata and fail-closed disposable database guards."""

import re
from uuid import UUID

from sqlalchemy import MetaData
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def guard_test_url(value: str | URL, expected_name: str) -> URL:
    url = make_url(value)
    if (
        not re.fullmatch(r"fleetiq_test_[0-9a-f]{32}", expected_name)
        or UUID(hex=expected_name.removeprefix("fleetiq_test_")).version != 4
        or url.database != expected_name
        or url.host not in {"127.0.0.1", "localhost"}
        or url.drivername != "postgresql+psycopg"
    ):
        raise ValueError("destructive operations require the exact loopback UUID test database")
    return url
