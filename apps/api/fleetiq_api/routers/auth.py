"""HttpOnly cookie sessions, strict origins and session-bound double-submit CSRF."""

import hmac
import secrets
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fleetiq_domain.identity import AuthenticationError
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import create_engine

from fleetiq_api.auth import AuthService, digest
from fleetiq_api.settings import Settings

router = APIRouter(prefix="/auth", tags=["authentication"])
ACCESS = "fleetiq_access"
REFRESH = "fleetiq_refresh"
CSRF = "fleetiq_csrf"


def get_auth_service(request: Request):
    if request.app.state.auth_service is None:
        settings = Settings()
        request.app.state.auth_service = AuthService(
            create_engine(settings.database_url.get_secret_value(), hide_parameters=True), settings
        )
    return request.app.state.auth_service


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    organization_code: str = Field(min_length=1, max_length=128)
    subject: str = Field(min_length=1, max_length=128)
    password: SecretStr


def origin(request, service):
    if request.headers.get("origin") not in service.settings.allowed_origins:
        raise HTTPException(403, "Origin rejected")


def csrf_check(request, service):
    origin(request, service)
    cookie = request.cookies.get(CSRF, "")
    header = request.headers.get("x-csrf-token", "")
    if not 32 <= len(cookie) <= 128 or not hmac.compare_digest(cookie, header):
        raise HTTPException(403, "CSRF rejected")
    return header


def secure_cookie(request, service):
    local = {"localhost", "127.0.0.1", "::1"}
    configured = service.settings.auth_cookie_secure
    return (
        request.url.hostname not in local
        or urlparse(service.settings.auth_issuer).hostname not in local
        or configured is True
    )


def set_cookie(response, request, service, name, value, max_age):
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        path="/auth" if name == REFRESH else "/",
        httponly=True,
        secure=secure_cookie(request, service),
        samesite="strict",
    )
    response.headers["Cache-Control"] = "no-store"


def install(response, request, service, issued):
    remaining = max(0, int((issued.expires_at - service.clock()).total_seconds()))
    set_cookie(response, request, service, ACCESS, issued.access, min(300, remaining))
    set_cookie(response, request, service, REFRESH, issued.refresh, remaining)
    set_cookie(response, request, service, CSRF, issued.csrf, remaining)


def access(request):
    authorization = request.headers.get("authorization")
    if authorization is not None:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(401, "Authentication required")
        return token
    token = request.cookies.get(ACCESS)
    if not token:
        raise HTTPException(401, "Authentication required")
    return token


@router.get("/csrf")
def bootstrap_csrf(
    request: Request, response: Response, service: AuthService = Depends(get_auth_service)
):
    origin(request, service)
    nonce = request.cookies.get(CSRF, "")
    if not 32 <= len(nonce) <= 128:
        nonce = secrets.token_urlsafe(32)
    set_cookie(response, request, service, CSRF, nonce, 1800)
    return {"csrf_token": nonce}


@router.post("/login")
def login(
    body: Login,
    request: Request,
    response: Response,
    service: AuthService = Depends(get_auth_service),
):
    nonce = csrf_check(request, service)
    if not 1 <= len(body.password.get_secret_value()) <= 128:
        raise HTTPException(401, "Invalid credentials")
    with service.engine.begin() as c:
        issued, limited = service.login(
            c,
            body.organization_code,
            body.subject,
            body.password.get_secret_value(),
            nonce,
            request.client.host if request.client else "unknown",
        )
    if limited:
        raise HTTPException(429, "Login limit exceeded", headers={"Retry-After": "300"})
    if issued is None:
        raise HTTPException(401, "Invalid credentials")
    install(response, request, service, issued)
    return {"authenticated": True, "expires_at": issued.expires_at, "csrf_token": issued.csrf}


@router.post("/refresh")
def refresh(request: Request, response: Response, service: AuthService = Depends(get_auth_service)):
    nonce = csrf_check(request, service)
    with service.engine.begin() as c:
        issued = service.refresh(c, request.cookies.get(REFRESH, ""), nonce)
    # Revocation caused by reuse must commit before returning the rejection.
    if issued is None:
        raise HTTPException(401, "Refresh rejected")
    install(response, request, service, issued)
    return {"authenticated": True, "expires_at": issued.expires_at, "csrf_token": issued.csrf}


@router.get("/me")
def current_user(
    request: Request, response: Response, service: AuthService = Depends(get_auth_service)
):
    try:
        with service.engine.begin() as c:
            row = service.authenticate(c, access(request))
    except AuthenticationError:
        raise HTTPException(401, "Authentication required") from None
    response.headers["Cache-Control"] = "no-store"
    return {
        "id": str(row["user_id"]),
        "organization_id": str(row["organization_id"]),
        "subject": row["subject"],
        "display_name": row["display_name"],
    }


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, service: AuthService = Depends(get_auth_service)):
    nonce = csrf_check(request, service)
    try:
        with service.engine.begin() as c:
            row = service.authenticate(c, access(request))
            if not hmac.compare_digest(digest(nonce), row["csrf_hash"]):
                raise HTTPException(403, "CSRF rejected")
            service.revoke(c, row, "User logout")
    except AuthenticationError:
        raise HTTPException(401, "Authentication required") from None
    for name in (ACCESS, REFRESH, CSRF):
        response.delete_cookie(
            name,
            path="/auth" if name == REFRESH else "/",
            httponly=True,
            secure=secure_cookie(request, service),
            samesite="strict",
        )
    response.headers["Cache-Control"] = "no-store"
