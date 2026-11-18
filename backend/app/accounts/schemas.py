import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class AlpacaCredentialsCreate(BaseModel):
    api_key: str = Field(min_length=1)
    api_secret: str = Field(min_length=1)
    is_paper: bool = True


class TradingAccountRead(BaseModel):
    """Deliberately excludes encrypted_api_key/encrypted_api_secret — never sent to a client."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: str
    is_paper: bool
    is_connected: bool
    last_verified_at: datetime | None = None


class LLMKeyCreate(BaseModel):
    """POST /accounts/llm (#191). The key is write-only: verified, encrypted, never echoed."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["openai", "anthropic"]
    api_key: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class LLMKeyRead(BaseModel):
    """The stored key's status; deliberately has no key field."""

    model_config = ConfigDict(from_attributes=True)

    provider: str
    is_connected: bool
    last_verified_at: datetime | None = None


class LLMKeyStatusRead(BaseModel):
    """GET /accounts/llm: the user's key status and what their cycles run on.

    ``effective_source`` is USER or SERVER; all three ``effective_*`` fields are null
    when the user has no key and LLM_SERVER_KEY_FALLBACK is off (cycles then fail).
    """

    connected: bool
    provider: str | None = None
    last_verified_at: datetime | None = None
    effective_source: str | None = None
    effective_provider: str | None = None
    effective_model: str | None = None
