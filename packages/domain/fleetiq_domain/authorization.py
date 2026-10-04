"""Fresh, tenant-scoped permission checks; role names confer no authority."""

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa

from fleetiq_domain.models.assets import Aircraft
from fleetiq_domain.models.operations import Role, RoleAssignment, User

PERMISSIONS = frozenset(
    {
        "fleet:read",
        "asset:write",
        "scenario:write",
        "workorder:draft",
        "technical:approve",
        "schedule:approve",
        "task:execute",
        "release:record",
        "inventory:write",
        "dataset:import",
        "model:train",
        "model:deploy",
        "users:manage",
        "audit:read",
    }
)


class Forbidden(PermissionError):
    """Identical rejection for absent and inaccessible resources."""


@dataclass(frozen=True)
class Principal:
    organization_id: UUID
    user_id: UUID


def grants(c, principal, permission):
    if permission not in PERMISSIONS:
        raise Forbidden("Access denied")
    rows = c.execute(
        sa.select(RoleAssignment.__table__, Role.permissions)
        .join(
            Role,
            sa.and_(
                Role.id == RoleAssignment.role_id,
                Role.organization_id == RoleAssignment.organization_id,
            ),
        )
        .join(
            User,
            sa.and_(
                User.id == RoleAssignment.user_id,
                User.organization_id == RoleAssignment.organization_id,
            ),
        )
        .where(
            RoleAssignment.organization_id == principal.organization_id,
            RoleAssignment.user_id == principal.user_id,
            RoleAssignment.active,
            User.active,
        )
    ).mappings()
    return [
        r for r in rows if isinstance(r["permissions"], list) and permission in r["permissions"]
    ]


def aircraft_filter(c, principal, permission):
    scopes = []
    for r in grants(c, principal, permission):
        if r["scope_kind"] == "organization":
            scopes.append(sa.true())
        elif r["scope_kind"] == "site":
            scopes.append(Aircraft.site_id == r["site_id"])
        elif r["scope_kind"] == "fleet":
            scopes.append(Aircraft.fleet_id == r["fleet_id"])
    return sa.and_(
        Aircraft.organization_id == principal.organization_id,
        sa.or_(*scopes) if scopes else sa.false(),
    )


def require(c, principal, permission, *, aircraft_id=None, site_id=None):
    if aircraft_id is not None:
        if not c.scalar(
            sa.select(Aircraft.id).where(
                Aircraft.id == aircraft_id, aircraft_filter(c, principal, permission)
            )
        ):
            raise Forbidden("Access denied")
        if site_id is not None and not c.scalar(
            sa.select(Aircraft.id).where(
                Aircraft.id == aircraft_id,
                Aircraft.site_id == site_id,
                Aircraft.organization_id == principal.organization_id,
            )
        ):
            raise Forbidden("Access denied")
        return
    for r in grants(c, principal, permission):
        if r["scope_kind"] == "organization" or (
            site_id is not None and r["scope_kind"] == "site" and r["site_id"] == site_id
        ):
            return
    raise Forbidden("Access denied")


def visible_scope(c, principal, permission, organization_id, scope_kind, scope_id):
    """Shared report/SSE rule. Unknown or mismatched scopes fail closed."""
    if organization_id != principal.organization_id:
        return False
    try:
        if scope_kind == "aircraft":
            if scope_id is None:
                return False
            require(c, principal, permission, aircraft_id=scope_id)
        elif scope_kind == "site":
            if scope_id is None:
                return False
            require(c, principal, permission, site_id=scope_id)
        elif scope_kind == "organization" and scope_id in (None, organization_id):
            require(c, principal, permission)
        elif scope_kind == "fleet" and scope_id is not None:
            return any(
                r["scope_kind"] == "organization"
                or (r["scope_kind"] == "fleet" and r["fleet_id"] == scope_id)
                for r in grants(c, principal, permission)
            )
        else:
            return False
    except Forbidden:
        return False
    return True
