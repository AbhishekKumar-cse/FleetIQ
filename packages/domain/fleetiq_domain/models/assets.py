"""Tenant-scoped identities; readiness is a later versioned projection."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from fleetiq_domain.db import Base


class Entity:
    id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class ScopedEntity(Entity):
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )


class Organization(Entity, Base):
    __tablename__ = "organization"
    __table_args__ = (UniqueConstraint("code", name="uq_organization_code"),)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)


class Site(ScopedEntity, Base):
    __tablename__ = "site"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_site_org_id"),
        UniqueConstraint("organization_id", "code", name="uq_site_org_code"),
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)


class Fleet(ScopedEntity, Base):
    __tablename__ = "fleet"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_fleet_org_id"),
        UniqueConstraint("organization_id", "code", name="uq_fleet_org_code"),
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)


class AircraftType(ScopedEntity, Base):
    __tablename__ = "aircraft_type"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_aircraft_type_org_id"),
        UniqueConstraint("organization_id", "code", name="uq_aircraft_type_org_code"),
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    configuration: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class Aircraft(ScopedEntity, Base):
    __tablename__ = "aircraft"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_aircraft_org_id"),
        UniqueConstraint("organization_id", "tail_label", name="uq_aircraft_org_tail"),
        ForeignKeyConstraint(
            ["organization_id", "type_id"],
            ["aircraft_type.organization_id", "aircraft_type.id"],
            name="fk_aircraft_org_type",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "fleet_id"],
            ["fleet.organization_id", "fleet.id"],
            name="fk_aircraft_org_fleet",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["site.organization_id", "site.id"],
            name="fk_aircraft_org_site",
            ondelete="RESTRICT",
        ),
        CheckConstraint("status_version >= 0", name="status_version"),
        Index("ix_aircraft_org_type", "organization_id", "type_id"),
        Index("ix_aircraft_org_fleet", "organization_id", "fleet_id"),
        Index("ix_aircraft_org_site", "organization_id", "site_id"),
    )
    tail_label: Mapped[str] = mapped_column(String(64), nullable=False)
    type_id: Mapped[UUID] = mapped_column(nullable=False)
    fleet_id: Mapped[UUID] = mapped_column(nullable=False)
    site_id: Mapped[UUID] = mapped_column(nullable=False)
    status_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
