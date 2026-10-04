from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fleetiq_domain.models.assets import Organization
from fleetiq_domain.models.components import Component
from fleetiq_domain.models.inventory import (
    Inventory,
    PartCompatibility,
    SerializedStock,
    SparePart,
    StockMovement,
    TaskPart,
    consume_reservation,
    release_reservation,
    reserve_part,
)
from fleetiq_domain.models.work import MaintenanceTask, ProcedureRevision, Recommendation, WorkOrder
from sqlalchemy import insert, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError

pytestmark = pytest.mark.integration


@pytest.fixture
def stock_fixture(domain_connection):
    c, ids = domain_connection
    org = ids["organization"]
    procedure = c.scalar(
        insert(ProcedureRevision)
        .values(
            organization_id=org,
            code="DEMO",
            revision="1",
            authority_label="Demo only",
            duration_slots=1,
            skills=[],
            part_requirements=[],
        )
        .returning(ProcedureRevision.id)
    )
    rec = c.scalar(
        insert(Recommendation)
        .values(
            organization_id=org,
            aircraft_id=ids["aircraft"],
            component_id=ids["component"],
            policy_version="demo-v1",
            urgency="routine",
            rationale={},
        )
        .returning(Recommendation.id)
    )
    work = c.scalar(
        insert(WorkOrder)
        .values(
            organization_id=org,
            aircraft_id=ids["aircraft"],
            component_id=ids["component"],
            recommendation_id=rec,
        )
        .returning(WorkOrder.id)
    )
    c.execute(
        insert(PartCompatibility).values(
            organization_id=org,
            part_id=ids["part"],
            aircraft_type_id=ids["type"],
            procedure_revision_id=procedure,
        )
    )
    tasks = [
        c.scalar(
            insert(MaintenanceTask)
            .values(
                organization_id=org,
                work_order_id=work,
                procedure_revision_id=procedure,
                duration_slots=1,
            )
            .returning(MaintenanceTask.id)
        )
        for _ in range(3)
    ]
    for task in tasks:
        c.execute(
            insert(TaskPart).values(
                organization_id=org, task_id=task, part_id=ids["part"], quantity=2
            )
        )
    row = dict(
        organization_id=org,
        part_id=ids["part"],
        site_id=ids["site"],
        condition="serviceable",
        on_hand=3,
        quarantined=1,
    )
    stock = c.scalar(insert(Inventory).values(**row).returning(Inventory.id))
    return c, ids | {"stock": stock, "tasks": tasks, "procedure": procedure}, row


def test_balance_location_and_tenant_constraints(stock_fixture):
    c, ids, row = stock_fixture
    for changes in (
        {"on_hand": -1},
        {"reserved": 3},
        {"quarantined": 4},
        {"on_hand": "NaN"},
        {"condition": "repairable", "reserved": 1},
    ):
        with pytest.raises(IntegrityError), c.begin_nested():
            c.execute(update(Inventory).where(Inventory.id == ids["stock"]).values(**changes))
    with pytest.raises(IntegrityError), c.begin_nested():
        c.execute(insert(Inventory).values(**row))
    foreign = c.scalar(
        insert(Organization).values(code="OTHER", name="Other").returning(Organization.id)
    )
    part = c.scalar(
        insert(SparePart)
        .values(organization_id=foreign, code="OTHER", kind="engine")
        .returning(SparePart.id)
    )
    with pytest.raises(IntegrityError), c.begin_nested():
        c.execute(insert(Inventory).values(**(row | {"part_id": part})))
    with pytest.raises(IntegrityError), c.begin_nested():
        c.execute(
            insert(Component).values(
                organization_id=ids["organization"], serial="INVALID", kind="engine", part_id=part
            )
        )
    with pytest.raises(IntegrityError), c.begin_nested():
        c.execute(
            insert(Component).values(
                organization_id=ids["organization"], serial="UNKNOWN", kind="engine"
            )
        )


def test_compatible_reservations_consume_once_and_release_hold(stock_fixture):
    c, ids, _ = stock_fixture
    org, stock = ids["organization"], ids["stock"]
    one, two, three = ids["tasks"]
    actor = ids["user"]

    def balance():
        return c.execute(
            select(Inventory.on_hand, Inventory.reserved, Inventory.quarantined).where(
                Inventory.id == stock
            )
        ).one()

    reservation = reserve_part(c, org, stock, one, 1)
    assert balance() == (3, 1, 1)
    with pytest.raises(ValueError, match="insufficient"):
        reserve_part(c, org, stock, two, 2)
    args = dict(actor_id=actor, idempotency_key="DEMO-CONSUME")
    movement = consume_reservation(c, org, reservation, **args)
    assert balance() == (2, 0, 1)
    assert consume_reservation(c, org, reservation, **args) == movement
    assert balance() == (2, 0, 1)
    with pytest.raises(ValueError, match="changed consumption"):
        consume_reservation(c, org, reservation, actor_id=uuid4(), idempotency_key="DEMO-CONSUME")
    with pytest.raises(ValueError, match="consumed"):
        release_reservation(c, org, reservation)
    held = reserve_part(c, org, stock, three, 1)
    assert balance() == (2, 1, 1)
    assert release_reservation(c, org, held)
    assert not release_reservation(c, org, held)
    assert balance() == (2, 0, 1)
    incompatible = c.scalar(
        insert(SparePart)
        .values(organization_id=org, code="INCOMPATIBLE", kind="other")
        .returning(SparePart.id)
    )
    with pytest.raises(DBAPIError), c.begin_nested():
        c.execute(
            insert(TaskPart).values(
                organization_id=org, task_id=two, part_id=incompatible, quantity=1
            )
        )
    bad_stock = c.scalar(
        insert(Inventory)
        .values(
            organization_id=org,
            part_id=incompatible,
            site_id=ids["site"],
            condition="serviceable",
            on_hand=5,
        )
        .returning(Inventory.id)
    )
    with pytest.raises(ValueError, match="compatible task"):
        reserve_part(c, org, bad_stock, two, 1)
    with pytest.raises(DBAPIError), c.begin_nested():
        c.execute(update(StockMovement).values(quantity=-2))
    with pytest.raises(DBAPIError), c.begin_nested():
        c.execute(
            insert(StockMovement).values(
                organization_id=org,
                inventory_id=stock,
                quantity=2,
                reason="invalid reversal",
                reverses_id=movement,
                idempotency_key="BAD-REVERSAL",
                actor_id=actor,
            )
        )


def test_serialized_quarantined_and_repairable_stock_are_separate(stock_fixture):
    c, ids, row = stock_fixture
    for condition in ("quarantined", "repairable"):
        stock = c.scalar(
            insert(Inventory)
            .values(**(row | {"condition": condition, "on_hand": 1, "quarantined": 0}))
            .returning(Inventory.id)
        )
        component = c.scalar(
            insert(Component)
            .values(
                organization_id=ids["organization"],
                serial=condition,
                kind="engine",
                part_id=ids["part"],
            )
            .returning(Component.id)
        )
        c.execute(
            insert(SerializedStock).values(
                organization_id=ids["organization"], component_id=component, inventory_id=stock
            )
        )
        with pytest.raises(ValueError, match="insufficient"):
            reserve_part(c, ids["organization"], stock, ids["tasks"][0], 1)
    with pytest.raises(DBAPIError), c.begin_nested():
        c.execute(
            insert(SerializedStock).values(
                organization_id=ids["organization"],
                component_id=ids["component"],
                inventory_id=ids["stock"],
            )
        )


def test_concurrent_reservations_and_consumption_cannot_double_debit(stock_fixture):
    c, ids, _ = stock_fixture
    engine = c.engine
    c.commit()  # Publish only this disposable database's fixture for independent transactions.
    barrier = Barrier(2)

    def allocate(task):
        barrier.wait(timeout=10)
        with engine.begin() as connection:
            try:
                return reserve_part(connection, ids["organization"], ids["stock"], task, 2)
            except ValueError as error:
                assert "insufficient usable stock" in str(error)
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(allocate, ids["tasks"][:2]))
    reservations = [r for r in results if r is not None]
    assert len(reservations) == 1
    actor = ids["user"]

    def consume(_):
        with engine.begin() as connection:
            return consume_reservation(
                connection,
                ids["organization"],
                reservations[0],
                actor_id=actor,
                idempotency_key="CONCURRENT-CONSUME",
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        movements = list(pool.map(consume, range(2)))
    assert movements[0] == movements[1]
    with engine.connect() as connection:
        assert connection.execute(
            select(Inventory.on_hand, Inventory.reserved, Inventory.quarantined).where(
                Inventory.id == ids["stock"]
            )
        ).one() == (1, 0, 1)
