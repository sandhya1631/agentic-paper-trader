"""Password reset tokens (#166): issue, log (in place of a real email), and consume.

The raw token is only ever held in memory — it is handed to the caller once and
never persisted; the database stores only its SHA-256 hash. Finding the owning
user from a submitted token therefore requires knowing the raw token, not just
read access to the database.
"""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import PASSWORD_RESET_TOKEN_EXPIRY_MINUTES
from app.db.models import PasswordResetToken, User

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def hash_reset_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


async def create_password_reset_token(db: AsyncSession, user: User) -> str:
    """Issue a new reset token for this user and stage it (caller commits).

    Any earlier unused token for this user is invalidated first, so only the
    most recently requested link can ever be used.
    """
    await db.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
        )
        .values(used_at=_utcnow())
    )
    raw_token = secrets.token_urlsafe(32)
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_reset_token(raw_token),
            expires_at=_utcnow() + timedelta(minutes=PASSWORD_RESET_TOKEN_EXPIRY_MINUTES),
        )
    )
    return raw_token


def log_password_reset_link(email: str, raw_token: str, frontend_base_url: str) -> None:
    """Stand-in for sending a real email (out of scope for #166): logs the link
    server-side only. Never logged or returned anywhere a client can read it directly.

    Uses .warning(), not .info(): this app never calls logging.basicConfig, so with
    no handler configured only WARNING+ reaches Python's default last-resort handler.
    An .info() call here would be silently dropped under a plain `uvicorn app.main:app`
    run - the same convention the LLM clients and scheduler already rely on elsewhere
    in this codebase for messages that must be visible without extra log setup.
    """
    logger.warning(
        "Password reset requested for %s: %s/reset-password?token=%s (expires in %s min)",
        email,
        frontend_base_url,
        raw_token,
        PASSWORD_RESET_TOKEN_EXPIRY_MINUTES,
    )


async def consume_password_reset_token(db: AsyncSession, raw_token: str) -> User | None:
    """Return the owning User if the token is valid, unused and unexpired, else None.

    Marks the token used in the same call; the caller still commits.
    """
    result = await db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == hash_reset_token(raw_token)
        )
    )
    reset_token = result.scalar_one_or_none()
    if (
        reset_token is None
        or reset_token.used_at is not None
        or reset_token.expires_at < _utcnow()
    ):
        return None
    reset_token.used_at = _utcnow()
    return await db.get(User, reset_token.user_id)
