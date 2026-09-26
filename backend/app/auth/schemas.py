import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# bcrypt hashes at most 72 bytes of input and raises ValueError beyond that.
# Enforce the limit here so an over-long password is a clean 422, not a 500.
BCRYPT_MAX_PASSWORD_BYTES = 72


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)

    @field_validator("password")
    @classmethod
    def password_within_bcrypt_limit(cls, value: str) -> str:
        if len(value.encode("utf-8")) > BCRYPT_MAX_PASSWORD_BYTES:
            raise ValueError(
                f"Password must be at most {BCRYPT_MAX_PASSWORD_BYTES} bytes"
            )
        return value


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
