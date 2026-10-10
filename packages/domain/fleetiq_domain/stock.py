"""Atomic, idempotent physical stock services; proposals never call these functions."""

import json
from contextlib import contextmanager
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from fleetiq_evaluation.splits import content_hash

from fleetiq_domain.authorization import require
from fleetiq_domain.models.components import Installation
from fleetiq_domain.models.inventory import (
    Inventory,
    PartReservation,
    SerializedStock,
    SparePart,
    StockMovement,
    TaskPart,
    consume_reservation,
    release_reservation,
    reserve_part,
)
from fleetiq_domain.models.operations import AuditEvent, EventOutbox
from fleetiq_domain.models.work import MaintenanceTask, WorkOrder


@contextmanager
def mutation(c):
    with c.begin_nested():
        prior = c.scalar(sa.text("SELECT current_setting('fleetiq.stock_mutation',true)")) or ""
        c.execute(sa.text("SELECT set_config('fleetiq.stock_mutation','allowed',true)"))
        yield
        c.execute(
            sa.text("SELECT set_config('fleetiq.stock_mutation',:prior,true)"), {"prior": prior}
        )


def amount(q):
    q = Decimal(str(q))
    if not q.is_finite() or q <= 0 or q != q.quantize(Decimal("0.000001")):
        raise ValueError("Positive finite stock quantity with at most six decimal places required")
    return q


def operation(c, principal, key, request, action):
    if not isinstance(key, str) or not key.strip() or len(key) > 200:
        raise ValueError("Bounded nonempty idempotency key required")
    org = principal.organization_id
    request = request | {"actor": str(principal.user_id)}
    digest = content_hash(request)
    c.execute(
        sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
        {"key": f"stock-op:{org}:{key}"},
    )
    old = (
        c.execute(
            sa.text(
                "SELECT request_hash,result FROM stock_operation WHERE organization_id=:org AND idempotency_key=:key"
            ),
            {"org": org, "key": key},
        )
        .mappings()
        .one_or_none()
    )
    if old:
        if old["request_hash"] != digest:
            raise ValueError("Idempotency key reused with changed request")
        return old["result"]
    with mutation(c):
        result = action()
        identity = c.scalar(
            sa.text(
                "INSERT INTO stock_operation(organization_id,idempotency_key,request_hash,result,actor_id) VALUES(:org,:key,:digest,CAST(:result AS jsonb),:actor) RETURNING id"
            ),
            {
                "org": org,
                "key": key,
                "digest": digest,
                "result": json.dumps(result),
                "actor": principal.user_id,
            },
        )
        c.execute(
            sa.insert(AuditEvent).values(
                organization_id=org,
                actor_id=principal.user_id,
                action="stock." + request["action"],
                target_kind="stock_operation",
                target_id=identity,
                scope_kind="organization",
                scope_id=org,
                reason=request["reason"],
                versions={"request_hash": digest},
            )
        )
        c.execute(
            sa.insert(EventOutbox).values(
                organization_id=org,
                scope_kind="organization",
                scope_id=org,
                kind="stock." + request["action"],
                payload={"operation_id": str(identity), **result},
            )
        )
        return result


def inventory(c, principal, identity, *, permission="inventory:write", lock=True):
    query = (
        sa.select(Inventory.__table__, SparePart.serialized)
        .join(
            SparePart,
            sa.and_(
                SparePart.id == Inventory.part_id,
                SparePart.organization_id == Inventory.organization_id,
            ),
        )
        .where(Inventory.id == identity, Inventory.organization_id == principal.organization_id)
    )
    row = (
        c.execute(query.with_for_update(of=Inventory.__table__) if lock else query).mappings().one()
    )
    if permission is not None:
        require(c, principal, permission, site_id=row["site_id"])
    return row


def reason(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Explicit stock reason required")
    return value


def task_scope(c, principal, task_id, permission="inventory:write"):
    # Same lock order as task execution: task then work, then sorted stock rows.
    task = (
        c.execute(
            sa.select(MaintenanceTask.__table__)
            .where(
                MaintenanceTask.id == task_id,
                MaintenanceTask.organization_id == principal.organization_id,
            )
            .with_for_update()
        )
        .mappings()
        .one()
    )
    work = (
        c.execute(
            sa.select(WorkOrder.__table__)
            .where(
                WorkOrder.id == task["work_order_id"],
                WorkOrder.organization_id == principal.organization_id,
            )
            .with_for_update()
        )
        .mappings()
        .one()
    )
    require(c, principal, permission, aircraft_id=work["aircraft_id"])
    from fleetiq_domain.workflow import approved_bindings

    approved_bindings(c, principal, work)
    binding = next(b for b in work["technical_scope"]["tasks"] if b["task_id"] == str(task_id))
    return task, work, binding


def reserve(c, principal, task_id, inventory_id, quantity, *, key, reason_text, component_id=None):
    q = amount(quantity)
    task, work, binding = task_scope(c, principal, task_id)
    if task["state"] != "pending" or work["state"] not in {
        "planner_draft",
        "schedule_proposed",
        "approved",
        "executing",
    }:
        raise ValueError("Approved pending task scope required")

    def action():
        row = inventory(c, principal, inventory_id)
        declared = next(
            (p for p in binding["part_requirements"] if p["part_id"] == str(row["part_id"])), None
        )
        if declared is None:
            raise ValueError("Part absent from approved procedure scope")
        if row["serialized"]:
            if q != 1 or component_id is None:
                raise ValueError("Serialized reservation requires one named component")
            serial = c.scalar(
                sa.select(SerializedStock.id)
                .where(
                    SerializedStock.organization_id == principal.organization_id,
                    SerializedStock.inventory_id == inventory_id,
                    SerializedStock.component_id == component_id,
                )
                .with_for_update()
            )
            installed = c.scalar(
                sa.select(Installation.id).where(
                    Installation.organization_id == principal.organization_id,
                    Installation.component_id == component_id,
                    Installation.removed_at.is_(None),
                )
            )
            if (
                not serial
                or installed
                or c.scalar(
                    sa.select(PartReservation.id).where(
                        PartReservation.organization_id == principal.organization_id,
                        PartReservation.component_id == component_id,
                        PartReservation.state == "reserved",
                    )
                )
            ):
                raise ValueError("Uninstalled available serial in serviceable stock required")
        elif component_id is not None:
            raise ValueError("Nonserialized stock cannot claim a component serial")
        existing = c.scalar(
            sa.select(TaskPart.quantity).where(
                TaskPart.organization_id == principal.organization_id,
                TaskPart.task_id == task_id,
                TaskPart.part_id == row["part_id"],
            )
        )
        if existing is None:
            c.execute(
                sa.insert(TaskPart).values(
                    organization_id=principal.organization_id,
                    task_id=task_id,
                    part_id=row["part_id"],
                    quantity=declared["quantity"],
                )
            )
        elif existing != Decimal(str(declared["quantity"])):
            raise ValueError("Task demand differs from approved scope")
        rid = reserve_part(c, principal.organization_id, inventory_id, task_id, q)
        c.execute(
            sa.update(PartReservation)
            .where(PartReservation.id == rid)
            .values(component_id=component_id)
        )
        return {"reservation_id": str(rid)}

    return operation(
        c,
        principal,
        key,
        {
            "action": "reserve",
            "task": str(task_id),
            "inventory": str(inventory_id),
            "quantity": str(q),
            "serial": str(component_id),
            "reason": reason(reason_text),
        },
        action,
    )


def cancel(c, principal, reservation_id, *, key, reason_text):
    r = (
        c.execute(
            sa.select(PartReservation.__table__).where(
                PartReservation.organization_id == principal.organization_id,
                PartReservation.id == reservation_id,
            )
        )
        .mappings()
        .one()
    )
    task_scope(c, principal, r["task_id"])

    def action():
        inventory(c, principal, r["inventory_id"])
        release_reservation(c, principal.organization_id, reservation_id)
        return {"reservation_id": str(reservation_id), "state": "released"}

    return operation(
        c,
        principal,
        key,
        {"action": "cancel", "reservation": str(reservation_id), "reason": reason(reason_text)},
        action,
    )


def issue_task(c, principal, task_id):
    """Invoked inside the workflow start transaction; resume never issues twice."""
    task, work, binding = task_scope(c, principal, task_id, "task:execute")
    rows = (
        c.execute(
            sa.select(PartReservation.__table__, Inventory.part_id)
            .join(
                Inventory,
                sa.and_(
                    Inventory.id == PartReservation.inventory_id,
                    Inventory.organization_id == PartReservation.organization_id,
                ),
            )
            .where(
                PartReservation.organization_id == principal.organization_id,
                PartReservation.task_id == task_id,
                PartReservation.state.in_(["reserved", "consumed"]),
            )
            .order_by(PartReservation.inventory_id, PartReservation.id)
        )
        .mappings()
        .all()
    )
    for p in binding["part_requirements"]:
        total = sum((r["quantity"] for r in rows if str(r["part_id"]) == p["part_id"]), Decimal(0))
        if total != Decimal(str(p["quantity"])):
            raise ValueError("Full approved spare reservation required before task start")
    if any(
        str(r["part_id"]) not in {p["part_id"] for p in binding["part_requirements"]} for r in rows
    ):
        raise ValueError("Reservation outside approved scope")

    def action():
        movements = []
        for r in rows:
            row = inventory(c, principal, r["inventory_id"], permission=None)
            if r["state"] == "consumed":
                continue
            if row["serialized"] and r["component_id"] is None:
                raise ValueError("Serial-specific reservation required")
            mid = consume_reservation(
                c,
                principal.organization_id,
                r["id"],
                actor_id=principal.user_id,
                idempotency_key="issue:" + str(r["id"]),
            )
            if r["component_id"]:
                c.execute(
                    sa.delete(SerializedStock).where(
                        SerializedStock.organization_id == principal.organization_id,
                        SerializedStock.component_id == r["component_id"],
                        SerializedStock.inventory_id == r["inventory_id"],
                    )
                )
            movements.append(str(mid))
        return {"task_id": str(task_id), "movements": movements}

    # The original task start actor remains the issue actor on subsequent resumes.
    if rows and all(r["state"] == "consumed" for r in rows):
        return {"task_id": str(task_id), "movements": []}
    return operation(
        c,
        principal,
        "task-start:" + str(task_id),
        {
            "action": "issue",
            "task": str(task_id),
            "reason": "Consume approved reservations at task start",
        },
        action,
    )


def receipt(c, principal, inventory_id, quantity, *, key, reason_text, component_ids=()):
    q = amount(quantity)
    inventory(c, principal, inventory_id, lock=False)

    def action():
        row = inventory(c, principal, inventory_id)
        if row["condition"] == "serviceable" and row["quarantined"]:
            raise ValueError("Mixed quarantine row requires reconciliation")
        if row["serialized"]:
            if q != len(set(component_ids)) or not component_ids:
                raise ValueError("Every received serialized unit must be named")
            for cid in sorted(component_ids, key=str):
                c.execute(
                    sa.insert(SerializedStock).values(
                        organization_id=principal.organization_id,
                        inventory_id=inventory_id,
                        component_id=cid,
                    )
                )
        elif component_ids:
            raise ValueError("Nonserialized receipt cannot name serials")
        c.execute(
            sa.update(Inventory)
            .where(Inventory.id == inventory_id)
            .values(on_hand=Inventory.on_hand + q, version=Inventory.version + 1)
        )
        mid = c.scalar(
            sa.insert(StockMovement)
            .values(
                organization_id=principal.organization_id,
                inventory_id=inventory_id,
                quantity=q,
                reason=reason_text,
                idempotency_key=key,
                actor_id=principal.user_id,
            )
            .returning(StockMovement.id)
        )
        return {"movement_id": str(mid)}

    return operation(
        c,
        principal,
        key,
        {
            "action": "receipt",
            "inventory": str(inventory_id),
            "quantity": str(q),
            "serials": sorted(map(str, component_ids)),
            "reason": reason(reason_text),
        },
        action,
    )


def transfer(
    c,
    principal,
    source_id,
    target_id,
    quantity,
    *,
    key,
    reason_text,
    component_ids=(),
    inspection=None,
):
    """Return/quarantine/repair transfer. Promotion requires independent inspection."""
    q = amount(quantity)
    for identity in (source_id, target_id):
        inventory(c, principal, identity, lock=False)

    def action():
        rows = {i: inventory(c, principal, i) for i in sorted({source_id, target_id}, key=str)}
        source, target = rows[source_id], rows[target_id]
        if (
            source_id == target_id
            or source["part_id"] != target["part_id"]
            or source["site_id"] != target["site_id"]
        ):
            raise ValueError("Distinct same-part/site condition rows required")
        if source["on_hand"] - source["quarantined"] - source["reserved"] < q:
            raise ValueError("Insufficient unreserved source units")
        if target["condition"] == "serviceable" and source["condition"] != "serviceable":
            require(c, principal, "release:record", site_id=target["site_id"])
            inbound = (
                c.execute(
                    sa.select(StockMovement.__table__).where(
                        StockMovement.organization_id == principal.organization_id,
                        StockMovement.id == UUID(inspection["movement_id"])
                        if inspection and inspection.get("movement_id")
                        else sa.false(),
                        StockMovement.inventory_id == source_id,
                        StockMovement.quantity >= q,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                not inbound
                or inbound["actor_id"] == principal.user_id
                or not inspection
                or inspection.get("result") != "pass"
                or not inspection.get("reference")
                or inspection.get("inventory_version") != source["version"]
            ):
                raise ValueError("Independent passing stock inspection required")
        if source["serialized"]:
            if q != len(set(component_ids)):
                raise ValueError("Exact serial transfer required")
            for cid in sorted(component_ids, key=str):
                serial = c.scalar(
                    sa.select(SerializedStock.id)
                    .where(
                        SerializedStock.organization_id == principal.organization_id,
                        SerializedStock.inventory_id == source_id,
                        SerializedStock.component_id == cid,
                    )
                    .with_for_update()
                )
                if not serial or c.scalar(
                    sa.select(PartReservation.id).where(
                        PartReservation.organization_id == principal.organization_id,
                        PartReservation.component_id == cid,
                        PartReservation.state == "reserved",
                    )
                ):
                    raise ValueError("Available source serial required")
                c.execute(
                    sa.update(SerializedStock)
                    .where(SerializedStock.id == serial)
                    .values(inventory_id=target_id)
                )
        elif component_ids:
            raise ValueError("Nonserialized transfer cannot name serials")
        movements = []
        for row, delta in ((source, -q), (target, q)):
            c.execute(
                sa.update(Inventory)
                .where(Inventory.id == row["id"])
                .values(on_hand=Inventory.on_hand + delta, version=Inventory.version + 1)
            )
            movements.append(
                str(
                    c.scalar(
                        sa.insert(StockMovement)
                        .values(
                            organization_id=principal.organization_id,
                            inventory_id=row["id"],
                            quantity=delta,
                            reason=reason_text,
                            idempotency_key=key + ":" + str(row["id"]),
                            actor_id=principal.user_id,
                        )
                        .returning(StockMovement.id)
                    )
                )
            )
        return {"movements": movements, "inspection": inspection}

    return operation(
        c,
        principal,
        key,
        {
            "action": "transfer",
            "source": str(source_id),
            "target": str(target_id),
            "quantity": str(q),
            "serials": sorted(map(str, component_ids)),
            "inspection": inspection,
            "reason": reason(reason_text),
        },
        action,
    )


def return_issued(c, principal, reservation_id, target_id, *, key, reason_text):
    r = (
        c.execute(
            sa.select(PartReservation.__table__).where(
                PartReservation.organization_id == principal.organization_id,
                PartReservation.id == reservation_id,
            )
        )
        .mappings()
        .one()
    )
    task_scope(c, principal, r["task_id"])
    inventory(c, principal, target_id, lock=False)

    def action():
        # Serialize returns with issue/cancellation for this task.
        c.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": f"{principal.organization_id}:{r['task_id']}"},
        )
        row = inventory(c, principal, target_id)
        original = inventory(c, principal, r["inventory_id"], lock=False)
        returned = c.scalar(
            sa.select(sa.func.count())
            .select_from(StockMovement)
            .where(
                StockMovement.organization_id == principal.organization_id,
                StockMovement.reservation_id == reservation_id,
                StockMovement.quantity > 0,
            )
        )
        if (
            r["state"] != "consumed"
            or returned
            or row["condition"] not in {"quarantined", "repairable"}
            or row["part_id"] != original["part_id"]
            or row["site_id"] != original["site_id"]
        ):
            raise ValueError(
                "Unreturned issued reservation and matching quarantine/repair row required"
            )
        if r["component_id"]:
            c.execute(
                sa.insert(SerializedStock).values(
                    organization_id=principal.organization_id,
                    inventory_id=target_id,
                    component_id=r["component_id"],
                )
            )
        c.execute(
            sa.update(Inventory)
            .where(Inventory.id == target_id)
            .values(on_hand=Inventory.on_hand + r["quantity"], version=Inventory.version + 1)
        )
        mid = c.scalar(
            sa.insert(StockMovement)
            .values(
                organization_id=principal.organization_id,
                inventory_id=target_id,
                reservation_id=reservation_id,
                task_id=r["task_id"],
                quantity=r["quantity"],
                actor_id=principal.user_id,
                idempotency_key=key,
                reason=reason_text,
            )
            .returning(StockMovement.id)
        )
        return {"movement_id": str(mid)}

    return operation(
        c,
        principal,
        key,
        {
            "action": "return",
            "reservation": str(reservation_id),
            "target": str(target_id),
            "reason": reason(reason_text),
        },
        action,
    )


def reverse_receipt(c, principal, movement_id, *, key, reason_text):
    movement = (
        c.execute(
            sa.select(StockMovement.__table__).where(
                StockMovement.organization_id == principal.organization_id,
                StockMovement.id == movement_id,
            )
        )
        .mappings()
        .one()
    )
    row = inventory(c, principal, movement["inventory_id"], lock=False)
    if (
        movement["quantity"] <= 0
        or movement["reverses_id"]
        or movement["reservation_id"]
        or row["serialized"]
    ):
        raise ValueError(
            "Only unallocated nonserialized receipts can be reversed; issued units require quarantined return"
        )

    def action():
        current = inventory(c, principal, movement["inventory_id"])
        q = movement["quantity"]
        if current["on_hand"] - current["reserved"] - current["quarantined"] < q:
            raise ValueError("Receipt units are no longer available")
        mid = c.scalar(
            sa.insert(StockMovement)
            .values(
                organization_id=principal.organization_id,
                inventory_id=current["id"],
                quantity=-q,
                reverses_id=movement_id,
                reason=reason_text,
                idempotency_key=key,
                actor_id=principal.user_id,
            )
            .returning(StockMovement.id)
        )
        c.execute(
            sa.update(Inventory)
            .where(Inventory.id == current["id"])
            .values(on_hand=Inventory.on_hand - q, version=Inventory.version + 1)
        )
        return {"movement_id": str(mid)}

    return operation(
        c,
        principal,
        key,
        {"action": "reverse", "movement": str(movement_id), "reason": reason(reason_text)},
        action,
    )
