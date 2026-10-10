"""Compatible parts, separated stock conditions and single-debit reservation accounting."""

from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import AddConstraint, CreateIndex

from fleetiq_domain.models.components import Component
from fleetiq_domain.models.schema import entity

SparePart = entity(
    "spare_part",
    {
        "code": "text:required",
        "kind": "text:required",
        "pack_size": "number:required:1",
        "serialized": "bool:required:false",
    },
    checks=("pack_size > 0 AND pack_size < 'Infinity'::numeric",),
    unique=(("code",),),
)
PartCompatibility = entity(
    "part_compatibility",
    {
        "part_id": "uuid:required",
        "aircraft_type_id": "uuid:required",
        "procedure_revision_id": "uuid:required",
    },
    refs={
        "part_id": "spare_part",
        "aircraft_type_id": "aircraft_type",
        "procedure_revision_id": "procedure_revision",
    },
    unique=(("part_id", "aircraft_type_id", "procedure_revision_id"),),
)
Inventory = entity(
    "inventory",
    {
        "part_id": "uuid:required",
        "site_id": "uuid:required",
        "condition": "text:required",
        "on_hand": "number:required:0",
        "reserved": "number:required:0",
        "quarantined": "number:required:0",
        "version": "int:required:0",
    },
    refs={"part_id": "spare_part", "site_id": "site"},
    unique=(("part_id", "site_id", "condition"),),
    checks=(
        "condition IN ('serviceable','quarantined','repairable')",
        "on_hand >= 0 AND on_hand < 'Infinity'::numeric AND quarantined >= 0 AND quarantined <= on_hand",
        "reserved >= 0 AND reserved <= on_hand-quarantined AND version >= 0",
        "condition='serviceable' OR reserved=0",
    ),
)
SerializedStock = entity(
    "serialized_stock",
    {
        "component_id": "uuid:required",
        "inventory_id": "uuid:required",
    },
    refs={"component_id": "component", "inventory_id": "inventory"},
    unique=(("component_id",),),
)
TaskPart = entity(
    "task_part",
    {
        "task_id": "uuid:required",
        "part_id": "uuid:required",
        "quantity": "number:required",
    },
    refs={"task_id": "maintenance_task", "part_id": "spare_part"},
    checks=("quantity > 0 AND quantity < 'Infinity'::numeric",),
    unique=(("task_id", "part_id"),),
)
PartReservation = entity(
    "part_reservation",
    {
        "task_id": "uuid:required",
        "inventory_id": "uuid:required",
        "plan_id": "uuid:optional",
        "component_id": "uuid:optional",
        "quantity": "number:required",
        "state": "text:required:'reserved'",
        "version": "int:required:0",
    },
    refs={"task_id": "maintenance_task", "inventory_id": "inventory", "component_id": "component"},
    checks=(
        "quantity > 0 AND quantity < 'Infinity'::numeric",
        "state IN ('reserved','consumed','released')",
        "version >= 0",
    ),
)
StockMovement = entity(
    "stock_movement",
    {
        "inventory_id": "uuid:required",
        "reservation_id": "uuid:optional",
        "task_id": "uuid:optional",
        "reverses_id": "uuid:optional",
        "quantity": "number:required",
        "reason": "text:required",
        "idempotency_key": "text:required",
        "actor_id": "uuid:required",
        "occurred_at": "time:required:now()",
    },
    refs={
        "inventory_id": "inventory",
        "reservation_id": "part_reservation",
        "task_id": "maintenance_task",
        "reverses_id": "stock_movement",
    },
    checks=(
        "quantity <> 0 AND quantity > '-Infinity'::numeric AND quantity < 'Infinity'::numeric",
        "reverses_id IS NULL OR reverses_id <> id",
    ),
    unique=(("idempotency_key",), ("reverses_id",)),
)
PurchaseOrder = entity(
    "purchase_order",
    {
        "part_id": "uuid:required",
        "site_id": "uuid:required",
        "quantity": "number:required",
        "expected_arrival": "time:required",
        "confirmed_arrival": "time:optional",
        "status": "text:required:'announced'",
        "supplier": "text:required",
        "lead_time_assumption": "json:required",
    },
    refs={"part_id": "spare_part", "site_id": "site"},
    checks=(
        "quantity > 0 AND quantity < 'Infinity'::numeric",
        "status IN ('announced','confirmed','received','cancelled')",
        "status <> 'received' OR confirmed_arrival IS NOT NULL",
    ),
)

# No fabricated part backfill: upgrade fails atomically if unidentified components exist.
Component.__table__.c.part_id.nullable = False
constraint = sa.ForeignKeyConstraint(
    ["organization_id", "part_id"],
    ["spare_part.organization_id", "spare_part.id"],
    name="fk_component_part_id",
    ondelete="RESTRICT",
)
Component.__table__.append_constraint(constraint)
active_index = sa.Index(
    "uq_active_task_stock_reservation",
    PartReservation.organization_id,
    PartReservation.task_id,
    PartReservation.inventory_id,
    unique=True,
    postgresql_where=sa.text("state='reserved'"),
)
EXTRA = [
    str(AddConstraint(constraint).compile(dialect=dialect())),
    "ALTER TABLE component ALTER COLUMN part_id SET NOT NULL",
    str(CreateIndex(active_index).compile(dialect=dialect())),
    "CREATE TRIGGER immutable_stock_movement BEFORE UPDATE OR DELETE ON stock_movement "
    "FOR EACH ROW EXECUTE FUNCTION fleetiq_immutable_evidence()",
    """CREATE OR REPLACE FUNCTION fleetiq_validate_task_part() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN IF NOT EXISTS(SELECT 1 FROM maintenance_task t JOIN work_order w ON
w.id=t.work_order_id AND w.organization_id=t.organization_id JOIN aircraft a ON
a.id=w.aircraft_id AND a.organization_id=w.organization_id JOIN part_compatibility p ON
p.aircraft_type_id=a.type_id AND p.procedure_revision_id=t.procedure_revision_id
AND p.organization_id=t.organization_id WHERE t.id=NEW.task_id
AND t.organization_id=NEW.organization_id AND p.part_id=NEW.part_id) THEN
RAISE EXCEPTION 'part incompatible with task aircraft and procedure'; END IF; RETURN NEW; END $$""",
    "CREATE TRIGGER validate_task_part BEFORE INSERT OR UPDATE ON task_part "
    "FOR EACH ROW EXECUTE FUNCTION fleetiq_validate_task_part()",
    """CREATE OR REPLACE FUNCTION fleetiq_validate_serialized_stock() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN IF NOT EXISTS(SELECT 1 FROM inventory i JOIN component c ON c.part_id=i.part_id
AND c.organization_id=i.organization_id JOIN spare_part p ON p.id=i.part_id
AND p.organization_id=i.organization_id WHERE i.id=NEW.inventory_id AND c.id=NEW.component_id
AND i.organization_id=NEW.organization_id AND p.serialized AND NOT EXISTS
(SELECT 1 FROM installation x WHERE x.organization_id=c.organization_id
AND x.component_id=c.id AND x.removed_at IS NULL)) THEN
RAISE EXCEPTION 'serialized stock requires matching uninstalled serialized component'; END IF;
RETURN NEW; END $$""",
    "CREATE TRIGGER validate_serialized_stock BEFORE INSERT OR UPDATE ON serialized_stock "
    "FOR EACH ROW EXECUTE FUNCTION fleetiq_validate_serialized_stock()",
    """CREATE OR REPLACE FUNCTION fleetiq_validate_reversal() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN IF NEW.reverses_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM stock_movement m
WHERE m.id=NEW.reverses_id AND m.organization_id=NEW.organization_id
AND m.inventory_id=NEW.inventory_id AND m.quantity=-NEW.quantity AND m.reverses_id IS NULL)
THEN RAISE EXCEPTION 'reversal must negate original movement in the same stock row'; END IF;
RETURN NEW; END $$""",
    "CREATE TRIGGER validate_reversal BEFORE INSERT ON stock_movement "
    "FOR EACH ROW EXECUTE FUNCTION fleetiq_validate_reversal()",
]
DOWN_EXTRA = [
    "ALTER TABLE component DROP CONSTRAINT fk_component_part_id",
    "ALTER TABLE component ALTER COLUMN part_id DROP NOT NULL",
    "DROP TRIGGER validate_task_part ON task_part",
    "DROP FUNCTION fleetiq_validate_task_part()",
    "DROP TRIGGER validate_serialized_stock ON serialized_stock",
    "DROP FUNCTION fleetiq_validate_serialized_stock()",
    "DROP TRIGGER validate_reversal ON stock_movement",
    "DROP FUNCTION fleetiq_validate_reversal()",
]


def _amount(quantity):
    value = Decimal(str(quantity))
    if not value.is_finite() or value <= 0:
        raise ValueError("positive finite quantity required")
    return value


def reserve_part(connection, organization_id, inventory_id, task_id, quantity, *, plan_id=None):
    """Hold compatible physical stock under a row lock; do not debit on_hand."""
    quantity = _amount(quantity)
    # Serialize all allocations for one task/part even across multiple stock rows.
    connection.execute(
        sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
        {"key": f"{organization_id}:{task_id}"},
    )
    row = (
        connection.execute(
            sa.select(Inventory.__table__)
            .where(Inventory.organization_id == organization_id, Inventory.id == inventory_id)
            .with_for_update()
        )
        .mappings()
        .one()
    )
    demand = connection.scalar(
        sa.select(TaskPart.quantity).where(
            TaskPart.organization_id == organization_id,
            TaskPart.task_id == task_id,
            TaskPart.part_id == row["part_id"],
        )
    )
    compatible = connection.scalar(
        sa.text("""SELECT EXISTS(SELECT 1 FROM maintenance_task t
JOIN work_order w ON w.id=t.work_order_id AND w.organization_id=t.organization_id
JOIN aircraft a ON a.id=w.aircraft_id AND a.organization_id=w.organization_id
JOIN part_compatibility p ON p.aircraft_type_id=a.type_id
AND p.procedure_revision_id=t.procedure_revision_id AND p.organization_id=t.organization_id
WHERE t.id=:task AND t.organization_id=:org AND p.part_id=:part AND a.site_id=:site)"""),
        {"task": task_id, "org": organization_id, "part": row["part_id"], "site": row["site_id"]},
    )
    held = connection.scalar(
        sa.select(sa.func.coalesce(sa.func.sum(PartReservation.quantity), 0))
        .join(Inventory, Inventory.id == PartReservation.inventory_id)
        .where(
            PartReservation.organization_id == organization_id,
            PartReservation.task_id == task_id,
            Inventory.part_id == row["part_id"],
            PartReservation.state.in_(["reserved", "consumed"]),
        )
    )
    if not compatible or demand is None or held + quantity > demand:
        raise ValueError("compatible task demand at the aircraft site required")
    if (
        row["condition"] != "serviceable"
        or row["on_hand"] - row["quarantined"] - row["reserved"] < quantity
    ):
        raise ValueError("insufficient usable stock")
    with connection.begin_nested():
        reservation = connection.scalar(
            sa.insert(PartReservation)
            .values(
                organization_id=organization_id,
                inventory_id=inventory_id,
                task_id=task_id,
                quantity=quantity,
                plan_id=plan_id,
            )
            .returning(PartReservation.id)
        )
        connection.execute(
            sa.update(Inventory)
            .where(Inventory.id == inventory_id, Inventory.organization_id == organization_id)
            .values(reserved=Inventory.reserved + quantity, version=Inventory.version + 1)
        )
    return reservation


def consume_reservation(connection, organization_id, reservation_id, *, actor_id, idempotency_key):
    """Consume once atomically; replay requires the identical reservation and actor."""
    _lock_reservation_task(connection, organization_id, reservation_id)
    r = (
        connection.execute(
            sa.select(PartReservation.__table__)
            .where(
                PartReservation.organization_id == organization_id,
                PartReservation.id == reservation_id,
            )
            .with_for_update()
        )
        .mappings()
        .one()
    )
    existing = (
        connection.execute(
            sa.select(StockMovement.__table__).where(
                StockMovement.organization_id == organization_id,
                StockMovement.idempotency_key == idempotency_key,
            )
        )
        .mappings()
        .one_or_none()
    )
    if existing:
        if existing["reservation_id"] != reservation_id or existing["actor_id"] != actor_id:
            raise ValueError("idempotency key reused with changed consumption")
        return existing["id"]
    if r["state"] != "reserved":
        raise ValueError("active reservation required")
    connection.execute(
        sa.select(Inventory.id)
        .where(Inventory.organization_id == organization_id, Inventory.id == r["inventory_id"])
        .with_for_update()
    ).one()
    with connection.begin_nested():
        movement = connection.scalar(
            sa.insert(StockMovement)
            .values(
                organization_id=organization_id,
                inventory_id=r["inventory_id"],
                reservation_id=reservation_id,
                task_id=r["task_id"],
                quantity=-r["quantity"],
                actor_id=actor_id,
                idempotency_key=idempotency_key,
                reason="consume reservation",
            )
            .returning(StockMovement.id)
        )
        connection.execute(
            sa.update(Inventory)
            .where(Inventory.id == r["inventory_id"], Inventory.organization_id == organization_id)
            .values(
                on_hand=Inventory.on_hand - r["quantity"],
                reserved=Inventory.reserved - r["quantity"],
                version=Inventory.version + 1,
            )
        )
        connection.execute(
            sa.update(PartReservation)
            .where(
                PartReservation.id == reservation_id,
                PartReservation.organization_id == organization_id,
            )
            .values(state="consumed", version=PartReservation.version + 1)
        )
    return movement


def release_reservation(connection, organization_id, reservation_id):
    """Return a hold to usable stock without changing physical stock."""
    _lock_reservation_task(connection, organization_id, reservation_id)
    row = (
        connection.execute(
            sa.select(PartReservation.__table__)
            .where(
                PartReservation.organization_id == organization_id,
                PartReservation.id == reservation_id,
            )
            .with_for_update()
        )
        .mappings()
        .one()
    )
    if row["state"] == "released":
        return False
    if row["state"] != "reserved":
        raise ValueError("consumed reservation cannot be released")
    connection.execute(
        sa.select(Inventory.id)
        .where(Inventory.organization_id == organization_id, Inventory.id == row["inventory_id"])
        .with_for_update()
    ).one()
    with connection.begin_nested():
        connection.execute(
            sa.update(Inventory)
            .where(
                Inventory.id == row["inventory_id"], Inventory.organization_id == organization_id
            )
            .values(reserved=Inventory.reserved - row["quantity"], version=Inventory.version + 1)
        )
        connection.execute(
            sa.update(PartReservation)
            .where(
                PartReservation.id == reservation_id,
                PartReservation.organization_id == organization_id,
            )
            .values(state="released", version=PartReservation.version + 1)
        )
    return True


def _lock_reservation_task(connection, organization_id, reservation_id):
    task = connection.execute(
        sa.select(PartReservation.task_id).where(
            PartReservation.organization_id == organization_id, PartReservation.id == reservation_id
        )
    ).scalar_one()
    connection.execute(
        sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
        {"key": f"{organization_id}:{task}"},
    )
