import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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
