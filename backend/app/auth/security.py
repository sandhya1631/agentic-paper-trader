"""AuthSecurity (JWT) — password hashing plus token issue/validate/refresh.

Mirrors the AuthSecurity class in the architecture diagram:
issuer, expirationMinutes, signingAlgorithm; issueToken / validateToken / refreshToken.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from app.core.config import get_settings

settings = get_settings()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed_password.encode("utf-8"))


def issue_token(user_id: str, expires_minutes: int | None = None) -> str:
    """AuthSecurity.issueToken — signs a JWT for the given user id."""
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or settings.jwt_expiration_minutes
    )
    payload = {"sub": user_id, "exp": expire, "iss": settings.jwt_issuer}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def validate_token(token: str) -> dict[str, Any]:
    """AuthSecurity.validateToken — decodes and verifies a JWT, raising on failure."""
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        issuer=settings.jwt_issuer,
    )


def refresh_token(token: str) -> str:
    """AuthSecurity.refreshToken — validates an existing token and issues a new one."""
    payload = validate_token(token)
    return issue_token(payload["sub"])
