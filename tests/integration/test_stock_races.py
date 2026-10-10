"""Named real-DB stock contention, retry and inspected-return fixtures."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

import pytest
import sqlalchemy as sa
from fleetiq_domain.models.inventory import Inventory, PartReservation
from fleetiq_domain.models.operations import Role
from fleetiq_domain.models.work import MaintenanceTask
from fleetiq_domain.stock import cancel, receipt, reserve, return_issued, reverse_receipt, transfer
from fleetiq_domain.workflow import (
    approve_scope,
    draft_work_order,
    review_recommendation,
    task_transition,
    transition,
)

pytest_plugins = ["test_workflow_transitions"]

pytestmark = pytest.mark.integration


def prepare(c, ids, users, rec):
    review_recommendation(
        c, users["engineer"], rec, 0, "accepted", reason="Review fixture evidence"
    )
    work = draft_work_order(c, users["planner"], rec, 1, reason="Draft fixture")
    approve_scope(c, users["engineer"], work, 0, [ids["procedure"]], reason="Approve fixture scope")
    task = c.scalar(sa.select(MaintenanceTask.id).where(MaintenanceTask.work_order_id == work))
    return work, task


def test_competing_serial_reservations_cancel_retry_and_task_start(workflow_fixture):
    app, migration, ids, users = workflow_fixture
    with app.begin() as c:
        jobs = [prepare(c, ids, users, ids[k]) for k in ("recommendation", "rejected")]
    barrier = Barrier(2)

    def allocate(index):
        barrier.wait(timeout=10)
        with app.begin() as c:
            try:
                return reserve(
                    c,
                    users["warehouse"],
                    jobs[index][1],
                    ids["stock"],
                    1,
                    key=f"race-{index}",
                    reason_text="Race for last spare",
                    component_id=ids["serial"],
                )
            except ValueError as e:
                assert "serial" in str(e) or "stock" in str(e)
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(allocate, range(2)))
    assert sum(r is not None for r in results) == 1
    winner = next(i for i, r in enumerate(results) if r)
    work, task = jobs[winner]
    rid = UUID(results[winner]["reservation_id"])
    with app.begin() as c:
        assert (
            reserve(
                c,
                users["warehouse"],
                task,
                ids["stock"],
                1,
                key=f"race-{winner}",
                reason_text="Race for last spare",
                component_id=ids["serial"],
            )
            == results[winner]
        )
        with pytest.raises(ValueError, match="changed request"):
            reserve(
                c,
                users["warehouse"],
                task,
                ids["stock"],
                1,
                key=f"race-{winner}",
                reason_text="Changed",
                component_id=ids["serial"],
            )
        cancel(c, users["warehouse"], rid, key="cancel", reason_text="Cancel draft")
        assert (
            cancel(c, users["warehouse"], rid, key="cancel", reason_text="Cancel draft")["state"]
            == "released"
        )
        rid = UUID(
            reserve(
                c,
                users["warehouse"],
                task,
                ids["stock"],
                1,
                key="replacement-hold",
                reason_text="Hold again",
                component_id=ids["serial"],
            )["reservation_id"]
        )
        transition(c, users["planner"], work, 1, "schedule_proposed", reason="Propose")
        scope = c.scalar(
            sa.text("SELECT technical_scope FROM work_order WHERE id=:id"), {"id": work}
        )
        transition(
            c,
            users["planner"],
            work,
            2,
            "schedule_approved",
            reason="Approve plan",
            plan_approval={
                "scope_hash": scope["scope_hash"],
                "plan_reference": "stock-fixture",
                "plan_version": 1,
            },
        )
        transition(c, users["technician"], work, 3, "executing", reason="Execute")
        task_transition(c, users["technician"], task, 0, "executing", reason="Start consumes once")
        with pytest.raises(ValueError, match="Stale"):
            task_transition(c, users["technician"], task, 0, "executing", reason="Duplicate start")
        assert c.execute(
            sa.select(Inventory.on_hand, Inventory.reserved).where(Inventory.id == ids["stock"])
        ).one() == (0, 0)
        assert (
            c.scalar(sa.select(PartReservation.state).where(PartReservation.id == rid))
            == "consumed"
        )
    with migration.begin() as c:
        repair = c.scalar(
            sa.insert(Inventory)
            .values(
                organization_id=ids["organization"],
                part_id=ids["part"],
                site_id=ids["site"],
                condition="repairable",
            )
            .returning(Inventory.id)
        )
        c.execute(
            sa.update(Role)
            .where(Role.code == "inspector", Role.organization_id == ids["organization"])
            .values(permissions=["release:record", "inventory:write"])
        )
    with app.begin() as c:
        returned = return_issued(
            c,
            users["warehouse"],
            rid,
            repair,
            key="return",
            reason_text="Removed spare needs inspection",
        )
        assert (
            return_issued(
                c,
                users["warehouse"],
                rid,
                repair,
                key="return",
                reason_text="Removed spare needs inspection",
            )
            == returned
        )
        with pytest.raises(ValueError, match="Unreturned"):
            return_issued(
                c,
                users["warehouse"],
                rid,
                repair,
                key="second-return",
                reason_text="Duplicate physical return",
            )
        with pytest.raises(ValueError, match="Independent"):
            transfer(
                c,
                users["inspector"],
                repair,
                ids["stock"],
                1,
                key="no-inspection",
                reason_text="Unsafe promotion",
                component_ids=[ids["serial"]],
            )
        version = c.scalar(sa.select(Inventory.version).where(Inventory.id == repair))
        transfer(
            c,
            users["inspector"],
            repair,
            ids["stock"],
            1,
            key="inspected",
            reason_text="Independent inspected repair",
            component_ids=[ids["serial"]],
            inspection={
                "movement_id": returned["movement_id"],
                "inventory_version": version,
                "result": "pass",
                "reference": "DEMO-inspection-1",
            },
        )
        assert c.scalar(sa.select(Inventory.on_hand).where(Inventory.id == ids["stock"])) == 1


def test_nonserialized_receipt_reversal_and_direct_app_write_guard(workflow_fixture):
    from fleetiq_domain.models.inventory import SparePart
    from sqlalchemy.exc import DBAPIError

    app, migration, ids, users = workflow_fixture
    with migration.begin() as c:
        part = c.scalar(
            sa.insert(SparePart)
            .values(organization_id=ids["organization"], code="CONSUMABLE", kind="filter")
            .returning(SparePart.id)
        )
        stock = c.scalar(
            sa.insert(Inventory)
            .values(
                organization_id=ids["organization"],
                part_id=part,
                site_id=ids["site"],
                condition="serviceable",
            )
            .returning(Inventory.id)
        )
    with app.begin() as c:
        with pytest.raises(DBAPIError), c.begin_nested():
            c.execute(sa.update(Inventory).where(Inventory.id == stock).values(on_hand=99))
        result = receipt(
            c, users["warehouse"], stock, 2, key="receipt", reason_text="Confirmed physical arrival"
        )
        assert (
            receipt(
                c,
                users["warehouse"],
                stock,
                2,
                key="receipt",
                reason_text="Confirmed physical arrival",
            )
            == result
        )
        reverse_receipt(
            c,
            users["warehouse"],
            UUID(result["movement_id"]),
            key="reverse",
            reason_text="Correct mistaken receipt",
        )
        assert c.scalar(sa.select(Inventory.on_hand).where(Inventory.id == stock)) == 0
        with pytest.raises(ValueError, match="no longer available"):
            reverse_receipt(
                c,
                users["warehouse"],
                UUID(result["movement_id"]),
                key="reverse-again",
                reason_text="Double reversal",
            )
