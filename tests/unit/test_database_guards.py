from uuid import uuid4

import pytest
from fleetiq_domain.db import guard_test_url
from fleetiq_domain.migrations import migration_config


def test_exact_uuid_database_allowed():
    name = f"fleetiq_test_{uuid4().hex}"
    url = f"postgresql+psycopg://migration@127.0.0.1:5433/{name}"
    assert guard_test_url(url, name).database == name
    assert migration_config(url, operation="downgrade", expected_test_database=name)


@pytest.mark.parametrize(
    "name",
    [
        "fleetiq",
        "postgres",
        "fleetiq_test_",
        "fleetiq_test_" + "0" * 32,
        'fleetiq_test_";DROP DATABASE fleetiq;--',
    ],
)
def test_operational_and_malformed_database_names_rejected(name):
    with pytest.raises(ValueError):
        guard_test_url(f"postgresql+psycopg://migration@127.0.0.1:5433/{name}", name)


def test_mismatched_name_remote_host_and_unguarded_downgrade_rejected():
    name = f"fleetiq_test_{uuid4().hex}"
    with pytest.raises(ValueError):
        guard_test_url("postgresql+psycopg://migration@127.0.0.1:5433/fleetiq", name)
    with pytest.raises(ValueError):
        guard_test_url(f"postgresql+psycopg://migration@remote:5433/{name}", name)
    with pytest.raises(ValueError):
        migration_config(
            "postgresql+psycopg://migration@127.0.0.1:5433/fleetiq", operation="downgrade"
        )
