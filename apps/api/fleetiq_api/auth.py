"""Database-backed authentication with atomic rotation and durable reuse revocation."""

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import sqlalchemy as sa
from fleetiq_domain.identity import (
    AccessTokens,
    AuthenticationError,
    hash_password,
    verify_password,
)
from fleetiq_domain.models.assets import Organization
from fleetiq_domain.models.operations import (
    AuditEvent,
    AuthRateLimit,
    RefreshTokenHistory,
    Session,
    User,
)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass
class IssuedSession:
    session_id: UUID
    expires_at: datetime
    access: str = field(repr=False)
    refresh: str = field(repr=False)
    csrf: str = field(repr=False)


class AuthService:
    def __init__(self, engine, settings, clock=None):
        self.engine = engine
        self.settings = settings
        self.clock = clock or (lambda: datetime.now(UTC))
        self.tokens = AccessTokens(settings)
        self.dummy_hash = hash_password(secrets.token_urlsafe(32))

    def rate_allowed(self, c, organization, subject, peer):
        now = self.clock()
        allowed = True
        for key in sorted(
            [digest("ip:" + peer), digest("account:" + organization + ":" + subject)]
        ):
            c.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": key}
            )
            row = (
                c.execute(sa.select(AuthRateLimit.__table__).where(AuthRateLimit.key_hash == key))
                .mappings()
                .one_or_none()
            )
            if row is None:
                c.execute(
                    sa.insert(AuthRateLimit).values(key_hash=key, window_start=now, attempts=1)
                )
            else:
                fresh = now >= row["window_start"] + timedelta(minutes=5)
                attempts = 1 if fresh else row["attempts"] + 1
                c.execute(
                    sa.update(AuthRateLimit)
                    .where(AuthRateLimit.key_hash == key)
                    .values(attempts=attempts, window_start=now if fresh else row["window_start"])
                )
                allowed = allowed and attempts <= 5
        return allowed

    def audit(self, c, org, user, action, target, reason):
        c.execute(
            sa.insert(AuditEvent).values(
                organization_id=org,
                actor_id=user,
                action=action,
                target_kind="auth_session",
                target_id=target,
                scope_kind="owner",
                scope_id=user,
                versions={},
                reason=reason,
            )
        )

    def login(self, c, organization, subject, password, csrf, peer):
        organization, subject = organization.upper(), subject.lower()
        if not self.rate_allowed(c, organization, subject, peer):
            return None, True
        u = (
            c.execute(
                sa.select(User.__table__)
                .join(Organization, Organization.id == User.organization_id)
                .where(Organization.code == organization, User.subject == subject)
            )
            .mappings()
            .one_or_none()
        )
        valid = verify_password(
            password, u["password_hash"] if u and u["active"] else self.dummy_hash
        )
        if u is None or not u["active"] or not valid:
            return None, False
        now = self.clock()
        sid = uuid4()
        refresh = f"{sid}.{secrets.token_urlsafe(48)}"
        expiry = now + timedelta(hours=8)
        c.execute(
            sa.insert(Session).values(
                id=sid,
                organization_id=u["organization_id"],
                user_id=u["id"],
                refresh_hash=digest(refresh),
                csrf_hash=digest(csrf),
                issued_at=now,
                last_active_at=now,
                expires_at=expiry,
                password_version=u["password_version"],
            )
        )
        self.audit(
            c, u["organization_id"], u["id"], "auth.login", sid, "Password authentication succeeded"
        )
        return IssuedSession(
            sid,
            expiry,
            self.tokens.issue(u["id"], u["organization_id"], sid, expires_at=expiry, now=now),
            refresh,
            csrf,
        ), False

    def session(self, c, sid, org=None, user=None):
        statement = (
            sa.select(
                Session.__table__,
                User.active,
                User.subject,
                User.display_name,
                User.password_version.label("current_password_version"),
            )
            .join(
                User,
                sa.and_(
                    User.id == Session.user_id, User.organization_id == Session.organization_id
                ),
            )
            .where(Session.id == sid)
        )
        if org is not None:
            statement = statement.where(Session.organization_id == org, Session.user_id == user)
        return c.execute(statement.with_for_update(of=Session.__table__)).mappings().one_or_none()

    def valid(self, row, *, now=None):
        now = self.clock() if now is None else now
        return (
            row is not None
            and row["active"]
            and not row["revoked"]
            and now < row["expires_at"]
            and now < row["last_active_at"] + timedelta(minutes=30)
            and row["password_version"] == row["current_password_version"]
        )

    def authenticate(self, c, access):
        claims = self.tokens.decode(access)
        row = self.session(c, UUID(claims["sid"]), UUID(claims["org"]), UUID(claims["sub"]))
        now = self.clock()
        if not self.valid(row, now=now):
            raise AuthenticationError("inactive session")
        c.execute(
            sa.update(Session)
            .where(Session.id == row["id"])
            .values(last_active_at=max(now, row["last_active_at"]))
        )
        return row

    def refresh(self, c, token, csrf):
        try:
            prefix, secret = token.split(".")
            sid = UUID(prefix)
            if not 43 <= len(secret) <= 128:
                return None
        except (ValueError, TypeError):
            return None
        row = self.session(c, sid)
        now = self.clock()
        if not self.valid(row, now=now):
            return None
        hashed = digest(token)
        if not hmac.compare_digest(hashed, row["refresh_hash"]):
            known = c.scalar(
                sa.select(RefreshTokenHistory.id).where(
                    RefreshTokenHistory.organization_id == row["organization_id"],
                    RefreshTokenHistory.session_id == sid,
                    RefreshTokenHistory.refresh_hash == hashed,
                )
            )
            if known:
                self.revoke(c, row, "Refresh token reuse detected")
            return None
        if not hmac.compare_digest(digest(csrf), row["csrf_hash"]):
            return None
        refresh = f"{sid}.{secrets.token_urlsafe(48)}"
        csrf = secrets.token_urlsafe(32)
        c.execute(
            sa.insert(RefreshTokenHistory).values(
                organization_id=row["organization_id"],
                session_id=sid,
                refresh_hash=hashed,
                consumed_at=now,
            )
        )
        c.execute(
            sa.update(Session)
            .where(Session.id == sid)
            .values(
                refresh_hash=digest(refresh),
                csrf_hash=digest(csrf),
                rotation=Session.rotation + 1,
                last_active_at=max(now, row["last_active_at"]),
            )
        )
        self.audit(
            c, row["organization_id"], row["user_id"], "auth.refresh", sid, "Opaque token rotated"
        )
        return IssuedSession(
            sid,
            row["expires_at"],
            self.tokens.issue(
                row["user_id"], row["organization_id"], sid, expires_at=row["expires_at"], now=now
            ),
            refresh,
            csrf,
        )

    def revoke(self, c, row, reason):
        c.execute(
            sa.update(Session)
            .where(Session.id == row["id"])
            .values(revoked=True, revoked_at=self.clock())
        )
        self.audit(c, row["organization_id"], row["user_id"], "auth.revoke", row["id"], reason)
