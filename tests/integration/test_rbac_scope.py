"""Authorization is fresh and independent of role labels and UI."""

from uuid import uuid4

import pytest
import sqlalchemy as sa
from fleetiq_domain.audit import audited_command, transition_work_authorized
from fleetiq_domain.authorization import (
    Forbidden,
    Principal,
    aircraft_filter,
    require,
    visible_scope,
)
from fleetiq_domain.models.assets import Aircraft
from fleetiq_domain.models.operations import AuditEvent, Role, RoleAssignment, User
from fleetiq_domain.models.work import Recommendation, WorkOrder

pytestmark = pytest.mark.integration


def grant(c, ids, permissions, scope="organization"):
    role = c.scalar(
        sa.insert(Role)
        .values(organization_id=ids["organization"], code=str(uuid4()), permissions=permissions)
        .returning(Role.id)
    )
    return c.scalar(
        sa.insert(RoleAssignment)
        .values(
            organization_id=ids["organization"],
            user_id=ids["user"],
            role_id=role,
            scope_kind=scope,
            site_id=ids["site"] if scope == "site" else None,
        )
        .returning(RoleAssignment.id)
    )


def test_scope_and_revocation(domain_connection):
    c, ids = domain_connection
    p = Principal(ids["organization"], ids["user"])
    assignment = grant(c, ids, ["fleet:read"], "site")
    require(c, p, "fleet:read", aircraft_id=ids["aircraft"])
    assert c.scalars(sa.select(Aircraft.id).where(aircraft_filter(c, p, "fleet:read"))).all() == [
        ids["aircraft"]
    ]
    for p2, aircraft in [(p, uuid4()), (Principal(uuid4(), ids["user"]), ids["aircraft"])]:
        with pytest.raises(Forbidden):
            require(c, p2, "fleet:read", aircraft_id=aircraft)
    assert visible_scope(c, p, "fleet:read", ids["organization"], "aircraft", ids["aircraft"])
    assert not visible_scope(c, p, "fleet:read", ids["organization"], "organization", None)
    c.execute(sa.update(RoleAssignment).where(RoleAssignment.id == assignment).values(active=False))
    with pytest.raises(Forbidden):
        require(c, p, "fleet:read", aircraft_id=ids["aircraft"])
    c.execute(sa.update(RoleAssignment).where(RoleAssignment.id == assignment).values(active=True))
    c.execute(sa.update(User).where(User.id == ids["user"]).values(active=False))
    with pytest.raises(Forbidden):
        require(c, p, "fleet:read", aircraft_id=ids["aircraft"])


@pytest.mark.parametrize("permissions", [["users:manage"], ["task:execute"], ["dataset:import"]])
def test_no_implicit_release_or_completed_edits(domain_connection, permissions):
    c, ids = domain_connection
    p = Principal(ids["organization"], ids["user"])
    grant(c, ids, permissions)
    recommendation = c.scalar(
        sa.insert(Recommendation)
        .values(
            organization_id=ids["organization"],
            aircraft_id=ids["aircraft"],
            component_id=ids["component"],
            policy_version="fixture",
            urgency="low",
            rationale={},
        )
        .returning(Recommendation.id)
    )
    work = c.scalar(
        sa.insert(WorkOrder)
        .values(
            organization_id=ids["organization"],
            aircraft_id=ids["aircraft"],
            component_id=ids["component"],
            recommendation_id=recommendation,
            state="closed",
        )
        .returning(WorkOrder.id)
    )
    for target in ["released", "accepted"]:
        with pytest.raises(Forbidden):
            transition_work_authorized(c, p, work, 0, target, reason="Forbidden edit")
    assert c.scalar(sa.select(sa.func.count()).select_from(AuditEvent)) == 0


def test_audit_failure_rolls_back_command(domain_connection):
    c, ids = domain_connection
    p = Principal(ids["organization"], ids["user"])
    grant(c, ids, ["asset:write"])

    def execute(connection):
        connection.execute(
            sa.update(Aircraft).where(Aircraft.id == ids["aircraft"]).values(status_version=1)
        )

    with pytest.raises(sa.exc.DataError):
        audited_command(
            c,
            p,
            "asset:write",
            target_kind="aircraft",
            target_id=ids["aircraft"],
            aircraft_id=ids["aircraft"],
            reason="Change",
            versions={"bad": float("nan")},
            execute=execute,
        )
    assert c.scalar(sa.select(Aircraft.status_version)) == 0
    audited_command(
        c,
        p,
        "asset:write",
        target_kind="aircraft",
        target_id=ids["aircraft"],
        aircraft_id=ids["aircraft"],
        reason="Change",
        execute=execute,
    )
    assert c.scalar(sa.select(sa.func.count()).select_from(AuditEvent)) == 1
