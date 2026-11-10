import uuid
from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, OAuth2PasswordBearer
from jwt import PyJWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import validate_token
from app.core.constants import OPERATOR_ROLES
from app.db.enums import UserRole
from app.db.models import User
from app.db.session import get_db

bearer_scheme = HTTPBearer(auto_error=False)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token", auto_error=False)

_CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    token_str: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Blocks unauthenticated access — raises 401 unless a valid JWT names a real user."""
    token = None
    if credentials and credentials.credentials:
        token = credentials.credentials
    elif token_str:
        token = token_str

    if not token:
        raise _CREDENTIALS_ERROR

    try:
        payload = validate_token(token)
        user_id = uuid.UUID(payload["sub"])
    except (PyJWTError, KeyError, ValueError) as exc:
        raise _CREDENTIALS_ERROR from exc

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise _CREDENTIALS_ERROR
    return user


def require_roles(*allowed_roles: UserRole) -> Callable:
    """Create a dependency that requires an authenticated user with an allowed role."""

    async def role_guard(
        current_user: User = Depends(get_current_user),
    ) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role for this operation",
            )
        return current_user

    return role_guard


require_operator = require_roles(*OPERATOR_ROLES)
