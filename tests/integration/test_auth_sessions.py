from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from fleetiq_api.auth import AuthService, digest
from fleetiq_api.main import create_app
from fleetiq_api.routers.auth import ACCESS, CSRF, REFRESH
from fleetiq_api.settings import Settings
from fleetiq_domain.identity import hash_password
from fleetiq_domain.models.assets import Organization
from fleetiq_domain.models.operations import RefreshTokenHistory, Session, User
from sqlalchemy import create_engine, insert, select, update
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.integration
ORIGIN = "http://localhost:3000"
PASSWORD = "Fictional-test-password-42"


@pytest.fixture
def auth_client(isolated_database):
    url, _ = isolated_database
    migration = create_engine(url, hide_parameters=True)
    org, user = uuid4(), uuid4()
    with migration.begin() as c:
        c.execute(insert(Organization).values(id=org, code="AUTH-DEMO", name="Auth fixture"))
        c.execute(
            insert(User).values(
                id=user,
                organization_id=org,
                subject="fixture",
                display_name="Fixture",
                password_hash=hash_password(PASSWORD),
            )
        )
    settings = Settings()
    application = create_engine(
        make_url(settings.database_url.get_secret_value()).set(database=url.database),
        hide_parameters=True,
    )
    service = AuthService(application, settings)
    try:
        with TestClient(
            create_app(service), base_url="http://localhost:8000", headers={"Origin": ORIGIN}
        ) as client:
            yield client, service, migration, org, user
    finally:
        application.dispose()
        migration.dispose()


def login(client, password=PASSWORD):
    csrf = client.get("/auth/csrf").json()["csrf_token"]
    response = client.post(
        "/auth/login",
        json={"organization_code": "AUTH-DEMO", "subject": "fixture", "password": password},
        headers={"X-CSRF-Token": csrf},
    )
    return response, csrf


def test_login_cookie_storage_rotation_and_logout(auth_client):
    client, service, engine, _, _ = auth_client
    response, csrf = login(client)
    assert response.status_code == 200
    assert client.get("/auth/me").json()["subject"] == "fixture"
    assert response.headers["cache-control"] == "no-store"
    assert all(
        "HttpOnly" in cookie and "SameSite=strict" in cookie
        for cookie in response.headers.get_list("set-cookie")
    )
    access = client.cookies[ACCESS]
    refresh = client.cookies[REFRESH]
    claims = service.tokens.decode(access)
    assert claims["exp"] - claims["iat"] == 300
    assert not {"password", "password_hash", "refresh", "roles", "permissions"} & claims.keys()
    with engine.connect() as c:
        row = c.execute(select(Session.__table__)).mappings().one()
        assert row["refresh_hash"] == digest(refresh) and row["refresh_hash"] != refresh
        assert row["csrf_hash"] == digest(csrf)
    rotated = client.post("/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert rotated.status_code == 200 and client.cookies[REFRESH] != refresh
    nonce = rotated.json()["csrf_token"]
    with engine.connect() as c:
        assert c.scalar(select(RefreshTokenHistory.refresh_hash)) == digest(refresh)
        assert c.scalar(select(Session.rotation)) == 1
    # Reload can recover the current nonce without breaking the session binding.
    assert client.get("/auth/csrf").json()["csrf_token"] == nonce
    access = client.cookies[ACCESS]
    assert client.post("/auth/logout", headers={"X-CSRF-Token": nonce}).status_code == 204
    assert client.get("/auth/me", headers={"Authorization": "Bearer " + access}).status_code == 401


def test_refresh_reuse_revokes_the_entire_session(auth_client):
    client, _, engine, _, _ = auth_client
    assert login(client)[0].status_code == 200
    old_refresh, old_csrf = client.cookies[REFRESH], client.cookies[CSRF]
    assert client.post("/auth/refresh", headers={"X-CSRF-Token": old_csrf}).status_code == 200
    replay = {"Cookie": f"{REFRESH}={old_refresh}; {CSRF}={old_csrf}", "X-CSRF-Token": old_csrf}
    assert client.post("/auth/refresh", headers=replay).status_code == 401
    assert client.get("/auth/me").status_code == 401
    with engine.connect() as c:
        assert c.scalar(select(Session.revoked)) is True


def test_backward_clock_keeps_activity_monotonic_without_extending_expiry(auth_client):
    client, service, engine, _, _ = auth_client
    response, csrf = login(client)
    assert response.status_code == 200
    with engine.connect() as c:
        original = c.execute(select(Session.__table__)).mappings().one()
    service.clock = lambda: original["issued_at"] - timedelta(milliseconds=50)
    assert client.get("/auth/me").status_code == 200
    assert client.post("/auth/refresh", headers={"X-CSRF-Token": csrf}).status_code == 200
    with engine.connect() as c:
        stored = c.execute(select(Session.__table__)).mappings().one()
        assert stored["last_active_at"] == original["last_active_at"]
        assert stored["expires_at"] == original["expires_at"]


def test_forged_wrong_audience_expired_and_missing_claim_tokens_rejected(auth_client):
    client, service, _, _, _ = auth_client
    assert login(client)[0].status_code == 200
    claims = service.tokens.decode(client.cookies[ACCESS])
    header = {"kid": service.tokens.kid}
    cases = [
        jwt.encode(claims, b"x" * 64, algorithm="HS256", headers=header),
        jwt.encode(claims, None, algorithm="none", headers=header),
        jwt.encode(
            claims | {"aud": "wrong"}, service.tokens.private, algorithm="RS256", headers=header
        ),
        jwt.encode(
            claims | {"iss": "wrong"}, service.tokens.private, algorithm="RS256", headers=header
        ),
        jwt.encode(
            claims
            | {"iat": claims["iat"] - 1000, "nbf": claims["nbf"] - 1000, "exp": claims["iat"] - 1},
            service.tokens.private,
            algorithm="RS256",
            headers=header,
        ),
        jwt.encode(
            {k: v for k, v in claims.items() if k != "jti"},
            service.tokens.private,
            algorithm="RS256",
            headers=header,
        ),
        jwt.encode(claims, service.tokens.private, algorithm="RS256", headers={"kid": "wrong"}),
        jwt.encode(
            claims,
            rsa.generate_private_key(public_exponent=65537, key_size=2048),
            algorithm="RS256",
            headers=header,
        ),
    ]
    for token in cases:
        assert (
            client.get("/auth/me", headers={"Authorization": "Bearer " + token}).status_code == 401
        )


def test_password_csrf_origin_and_durable_login_limits(auth_client):
    client, _, _, _, _ = auth_client
    nonce = client.get("/auth/csrf").json()["csrf_token"]
    body = {"organization_code": "AUTH-DEMO", "subject": "fixture", "password": PASSWORD}
    assert client.post("/auth/login", json=body).status_code == 403
    assert (
        client.post(
            "/auth/login",
            json=body,
            headers={"X-CSRF-Token": nonce, "Origin": "https://attacker.example"},
        ).status_code
        == 403
    )
    for _ in range(5):
        assert login(client, "incorrect-password")[0].status_code == 401
    assert login(client)[0].status_code == 429


def test_unknown_refresh_cannot_revoke_and_session_idle_absolute_expiry(auth_client):
    client, service, engine, _, _ = auth_client
    _, nonce = login(client)
    valid_refresh = client.cookies[REFRESH]
    sid = valid_refresh.split(".")[0]
    guessed = f"{sid}." + "x" * 64
    assert (
        client.post(
            "/auth/refresh",
            headers={"Cookie": f"{REFRESH}={guessed}; {CSRF}={nonce}", "X-CSRF-Token": nonce},
        ).status_code
        == 401
    )
    assert client.get("/auth/me").status_code == 200
    service.clock = lambda: datetime.now(UTC) + timedelta(minutes=31)
    assert client.get("/auth/me").status_code == 401
    service.clock = lambda: datetime.now(UTC)
    with engine.begin() as c:
        now = datetime.now(UTC)
        c.execute(
            update(Session).values(
                issued_at=now - timedelta(hours=9),
                last_active_at=now - timedelta(hours=1, minutes=1),
                expires_at=now - timedelta(hours=1),
            )
        )
    assert client.get("/auth/me").status_code == 401


def test_secure_cookie_outside_localhost_and_session_scope(auth_client):
    client, service, engine, _, user = auth_client
    assert login(client)[0].status_code == 200
    claims = service.tokens.decode(client.cookies[ACCESS])
    wrong_scope = jwt.encode(
        claims | {"org": str(uuid4())},
        service.tokens.private,
        algorithm="RS256",
        headers={"kid": service.tokens.kid},
    )
    assert (
        client.get("/auth/me", headers={"Authorization": "Bearer " + wrong_scope}).status_code
        == 401
    )
    settings = service.settings.model_copy(
        update={
            "allowed_origins": ["https://fleet.example"],
            "auth_issuer": "https://fleet.example",
            "auth_cookie_secure": False,
        }
    )
    external = AuthService(service.engine, settings)
    with TestClient(
        create_app(external),
        base_url="https://fleet.example",
        headers={"Origin": "https://fleet.example"},
    ) as secure:
        response = secure.get("/auth/csrf")
        assert (
            "Secure" in response.headers["set-cookie"]
            and "HttpOnly" in response.headers["set-cookie"]
        )
    with engine.begin() as c:
        c.execute(update(User).where(User.id == user).values(active=False))
    assert client.get("/auth/me").status_code == 401
