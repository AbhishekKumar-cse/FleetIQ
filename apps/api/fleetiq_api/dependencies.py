"""Authenticated domain transactions with session-bound CSRF for mutations."""

import hmac

from fastapi import HTTPException, Request
from fleetiq_domain.authorization import Forbidden, Principal
from fleetiq_domain.identity import AuthenticationError

from fleetiq_api.auth import digest
from fleetiq_api.routers.auth import access, csrf_check, get_auth_service


def authorized_transaction(request: Request):
    service = get_auth_service(request)
    try:
        with service.engine.begin() as c:
            row = service.authenticate(c, access(request))
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                nonce = csrf_check(request, service)
                if not hmac.compare_digest(digest(nonce), row["csrf_hash"]):
                    raise HTTPException(403, "CSRF rejected")
            yield c, Principal(row["organization_id"], row["user_id"])
    except AuthenticationError:
        raise HTTPException(401, "Authentication required") from None
    except Forbidden:
        raise HTTPException(403, "Access denied") from None
