"""Argon2id credentials and a strict, pinned RS256 access-token contract."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError

PASSWORDS = PasswordHash.recommended()


class AuthenticationError(ValueError):
    pass


def hash_password(password):
    if not 12 <= len(password) <= 128:
        raise ValueError("password must contain 12 to 128 characters")
    result = PASSWORDS.hash(password)
    if not result.startswith("$argon2id$"):
        raise ValueError("Argon2id required")
    return result


def verify_password(password, encoded):
    if not 1 <= len(password) <= 128 or not encoded.startswith("$argon2id$"):
        return False
    try:
        return PASSWORDS.verify(password, encoded)
    except (ValueError, UnknownHashError, InvalidHashError, VerificationError):
        return False


class AccessTokens:
    def __init__(self, settings):
        self.issuer = settings.auth_issuer
        self.audience = settings.auth_audience
        self.kid = settings.auth_key_id
        self.private = serialization.load_pem_private_key(
            settings.auth_private_key_path.read_bytes(), password=None
        )
        self.public = serialization.load_pem_public_key(settings.auth_public_key_path.read_bytes())
        if not isinstance(self.private, rsa.RSAPrivateKey) or not isinstance(
            self.public, rsa.RSAPublicKey
        ):
            raise ValueError("RSA signing keys required")
        if (
            self.private.key_size < 2048
            or self.private.public_key().public_numbers() != self.public.public_numbers()
        ):
            raise ValueError("matching RSA keys of at least 2048 bits required")

    def issue(self, user_id, organization_id, session_id, *, expires_at, now=None):
        now = now or datetime.now(UTC)
        expiry = min(now + timedelta(minutes=5), expires_at)
        claims = {
            "sub": str(user_id),
            "org": str(organization_id),
            "sid": str(session_id),
            "jti": str(uuid4()),
            "iss": self.issuer,
            "aud": self.audience,
            "iat": int(now.timestamp()),
            "nbf": int(now.timestamp()),
            "exp": int(expiry.timestamp()),
            "typ": "access",
        }
        return jwt.encode(claims, self.private, algorithm="RS256", headers={"kid": self.kid})

    def decode(self, token):
        try:
            header = jwt.get_unverified_header(token)
            if (
                header.get("alg") != "RS256"
                or header.get("kid") != self.kid
                or header.get("typ") != "JWT"
            ):
                raise AuthenticationError("invalid access token")
            claims = jwt.decode(
                token,
                self.public,
                algorithms=["RS256"],
                issuer=self.issuer,
                audience=self.audience,
                options={
                    "require": [
                        "sub",
                        "org",
                        "sid",
                        "jti",
                        "iss",
                        "aud",
                        "iat",
                        "nbf",
                        "exp",
                        "typ",
                    ],
                    "strict_aud": True,
                },
            )
            if claims["typ"] != "access" or not 0 < claims["exp"] - claims["iat"] <= 300:
                raise AuthenticationError("invalid access token")
            for name in ("sub", "org", "sid", "jti"):
                UUID(claims[name])
            return claims
        except (jwt.PyJWTError, TypeError, ValueError, KeyError) as error:
            raise AuthenticationError("invalid access token") from error
