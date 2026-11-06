import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.constants import BCRYPT_MAX_PASSWORD_BYTES
from app.db.enums import UserRole

# bcrypt hashes at most 72 bytes of input and raises ValueError beyond that.
# Enforce the limit here so an over-long password is a clean 422, not a 500.


def _ensure_within_bcrypt_limit(value: str) -> str:
    if len(value.encode("utf-8")) > BCRYPT_MAX_PASSWORD_BYTES:
        raise ValueError(
            f"Password must be at most {BCRYPT_MAX_PASSWORD_BYTES} bytes"
        )
    return value


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)

    @field_validator("password")
    @classmethod
    def password_within_bcrypt_limit(cls, value: str) -> str:
        return _ensure_within_bcrypt_limit(value)


class UserLogin(BaseModel):
    email: EmailStr
    password: str

    # Guard login too: bcrypt.checkpw() raises ValueError beyond 72 bytes, which
    # would surface as a 500. Reject at the boundary as a clean 422 (same as register).
    @field_validator("password")
    @classmethod
    def password_within_bcrypt_limit(cls, value: str) -> str:
        return _ensure_within_bcrypt_limit(value)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    # Exposed so the frontend can gate admin-only controls (e.g. the kill switch).
    role: UserRole


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    # Always this exact message, whether or not the email is registered (#166):
    # the response must not be usable to enumerate accounts.
    detail: str = "If that email is registered, a password reset link has been sent."


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1)
    new_password: str = Field(min_length=8)

    @field_validator("new_password")
    @classmethod
    def password_within_bcrypt_limit(cls, value: str) -> str:
        return _ensure_within_bcrypt_limit(value)


class ResetPasswordResponse(BaseModel):
    detail: str = "Password has been reset. You can now log in with your new password."
