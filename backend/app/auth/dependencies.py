import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import validate_token
from app.db.models import User
from app.db.session import get_db

bearer_scheme = HTTPBearer()

_CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Blocks unauthenticated access — raises 401 unless a valid JWT names a real user."""
    try:
        payload = validate_token(credentials.credentials)
        user_id = uuid.UUID(payload["sub"])
    except (PyJWTError, KeyError, ValueError) as exc:
        raise _CREDENTIALS_ERROR from exc

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise _CREDENTIALS_ERROR
    return user
