"""Authorized commands and their audit evidence share a savepoint/transaction."""

import sqlalchemy as sa

from fleetiq_domain.authorization import Forbidden, require
from fleetiq_domain.models.operations import AuditEvent
from fleetiq_domain.models.work import TRANSITION_PERMISSIONS, WorkOrder, transition_work


def audited_command(
    c,
    principal,
    permission,
    *,
    target_kind,
    target_id,
    reason,
    execute,
    aircraft_id=None,
    site_id=None,
    versions=None,
):
    if not reason or not reason.strip():
        raise ValueError("A reason is required")
    with c.begin_nested():
        require(c, principal, permission, aircraft_id=aircraft_id, site_id=site_id)
        result = execute(c)
        c.execute(
            sa.insert(AuditEvent).values(
                organization_id=principal.organization_id,
                actor_id=principal.user_id,
                action=permission,
                target_kind=target_kind,
                target_id=target_id,
                scope_kind="aircraft" if aircraft_id else "site" if site_id else "organization",
                scope_id=aircraft_id or site_id or principal.organization_id,
                versions=versions or {},
                reason=reason,
            )
        )
        return result


def transition_work_authorized(
    c, principal, work_id, expected_version, target, *, reason, approval_scope=None
):
    permission = TRANSITION_PERMISSIONS.get(target)
    if permission is None:
        raise Forbidden("Access denied")
    row = (
        c.execute(
            sa.select(WorkOrder.__table__)
            .where(WorkOrder.id == work_id, WorkOrder.organization_id == principal.organization_id)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if row is None:
        raise Forbidden("Access denied")
    return audited_command(
        c,
        principal,
        permission,
        target_kind="work_order",
        target_id=work_id,
        aircraft_id=row["aircraft_id"],
        reason=reason,
        versions={"previous": expected_version, "next": expected_version + 1},
        execute=lambda connection: transition_work(
            connection,
            principal.organization_id,
            work_id,
            expected_version,
            target,
            actor_id=principal.user_id,
            permissions={permission},
            reason=reason,
            approval_scope=approval_scope,
        ),
    )
