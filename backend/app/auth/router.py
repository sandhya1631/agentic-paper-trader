from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.schemas import Token, UserCreate, UserLogin, UserRead
from app.auth.security import hash_password, issue_token, verify_password
from app.db.models import User
from app.db.session import get_db

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
    description="Create a new user account with email and password.",
)
async def register(payload: UserCreate, db: AsyncSession = Depends(get_db)) -> User:
    existing = await db.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        )

    user = User(email=payload.email, hashed_password=hash_password(payload.password))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post(
    "/login",
    response_model=Token,
    summary="User login (JSON format)",
    description=(
        "Authenticate user with JSON payload containing email and password, "
        "returning a JWT access token."
    ),
)
async def login(payload: UserLogin, db: AsyncSession = Depends(get_db)) -> Token:
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )

    return Token(access_token=issue_token(str(user.id)))


@router.post(
    "/token",
    response_model=Token,
    summary="OAuth2 Form Login (Swagger UI compatible)",
    description=(
        "Form-encoded authentication endpoint enabling native 'Authorize' dialog testing "
        "in Swagger UI. Enter email into Username field."
    ),
)
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
) -> Token:
    result = await db.execute(select(User).where(User.email == form_data.username))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return Token(access_token=issue_token(str(user.id)))


@router.get(
    "/me",
    response_model=UserRead,
    summary="Get current user profile",
    description=(
        "Protected route returning current authenticated user details. "
        "Requires Bearer JWT token in Authorization header."
    ),
)
async def me(current_user: User = Depends(get_current_user)) -> User:
    """Protected route — proves unauthenticated requests are blocked."""
    return current_user

