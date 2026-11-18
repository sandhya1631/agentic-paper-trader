"""Users' own OpenAI / Claude API keys (#191): the vault and the per-owner LLM client.

Mirrors the Alpaca vault (credential_vault.py): the key is Fernet-encrypted with the same
CREDENTIAL_ENCRYPTION_KEY, never returned, and decrypted only to build one client for one
cycle or replay. A cycle uses its agent OWNER's key; a replay uses the caller's.

No user key: the server's LLM configuration (LLM_PROVIDER and its key), today's behavior,
unless LLM_SERVER_KEY_FALLBACK is off, in which case the cycle fails with LLM_UNAVAILABLE
before any LLM call. A rejected user key fails the same way and never falls back to the
server key.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.credential_vault import (
    CredentialVaultError,
    decrypt_credential,
    encrypt_credential,
)
from app.core.config import Settings, get_settings
from app.core.constants import LLM_KEY_SOURCE_SERVER, LLM_KEY_SOURCE_USER
from app.db.models import AgentConfig, LLMCredential, TradingAccount, utcnow
from app.llm.base import LLMClient, LLMUnavailableError
from app.llm.factory import configured_model, create_user_llm_client

NO_KEY_AND_NO_FALLBACK = (
    "No AI provider key is connected and the server's key is turned off "
    "(LLM_SERVER_KEY_FALLBACK=false): connect your OpenAI or Claude key in Settings"
)


@dataclass(frozen=True)
class EffectiveLLM:
    """Which provider, model and key (USER or SERVER) a user's cycles run on."""

    provider: str
    model: str
    key_source: str


@dataclass(frozen=True)
class OwnerLLMClient:
    client: LLMClient
    effective: EffectiveLLM


async def get_llm_credential(db: AsyncSession, user_id: uuid.UUID) -> LLMCredential | None:
    """The user's row (key still encrypted), connected or revoked, or None."""
    result = await db.execute(select(LLMCredential).where(LLMCredential.user_id == user_id))
    return result.scalar_one_or_none()


async def save_llm_credential(
    db: AsyncSession, user_id: uuid.UUID, provider: str, api_key: str
) -> LLMCredential:
    """Encrypt and store the user's verified key, replacing any earlier provider and key.

    Stages the change without committing, so the caller commits it with its audit row.
    """
    credential = await get_llm_credential(db, user_id)
    if credential is None:
        credential = LLMCredential(user_id=user_id)
        db.add(credential)
    credential.provider = provider
    credential.encrypted_api_key = encrypt_credential(api_key)
    credential.is_connected = True
    credential.last_verified_at = utcnow()
    await db.flush()
    return credential


async def revoke_llm_credential(db: AsyncSession, user_id: uuid.UUID) -> LLMCredential | None:
    """Clear the stored key and mark it disconnected (staged; the caller commits).

    Returns the revoked row, or None if nothing was connected.
    """
    credential = await get_llm_credential(db, user_id)
    if credential is None or not credential.is_connected:
        return None
    credential.is_connected = False
    credential.encrypted_api_key = ""
    await db.flush()
    return credential


def effective_llm(credential: LLMCredential | None, settings: Settings) -> EffectiveLLM | None:
    """What the user's cycles would run on now; None when they can't run (no key, no
    fallback). Reads no key material."""
    if credential is not None and credential.is_connected:
        return EffectiveLLM(
            provider=credential.provider,
            model=configured_model(settings, credential.provider),
            key_source=LLM_KEY_SOURCE_USER,
        )
    if not settings.llm_server_key_fallback:
        return None
    return EffectiveLLM(
        provider=settings.llm_provider.lower(),
        model=configured_model(settings),
        key_source=LLM_KEY_SOURCE_SERVER,
    )


async def resolve_user_llm(
    db: AsyncSession, user_id: uuid.UUID, *, server_client: Callable[[], LLMClient]
) -> OwnerLLMClient:
    """The LLM client for one of this user's cycles or replays.

    A connected user key gets a fresh client (never cached or shared). Otherwise
    ``server_client()`` (the cached server-wide client) if the fallback is on. Raises
    LLMUnavailableError, with no key material in the message, when neither applies or
    the stored key can't be decrypted.
    """
    settings = get_settings()
    credential = await get_llm_credential(db, user_id)
    effective = effective_llm(credential, settings)
    if effective is None:
        raise LLMUnavailableError(NO_KEY_AND_NO_FALLBACK)
    if credential is None or effective.key_source == LLM_KEY_SOURCE_SERVER:
        return OwnerLLMClient(client=server_client(), effective=effective)
    try:
        api_key = decrypt_credential(credential.encrypted_api_key)
    except CredentialVaultError:
        raise LLMUnavailableError(
            "Your stored AI provider key can't be decrypted: reconnect it in Settings"
        ) from None
    client = create_user_llm_client(credential.provider, api_key, settings)
    return OwnerLLMClient(client=client, effective=effective)


async def resolve_agent_llm(
    db: AsyncSession, agent: AgentConfig, *, server_client: Callable[[], LLMClient]
) -> OwnerLLMClient:
    """``resolve_user_llm`` for the agent's owner (the user who owns its account)."""
    owner_id = (
        await db.execute(
            select(TradingAccount.user_id).where(TradingAccount.id == agent.account_id)
        )
    ).scalar_one()
    return await resolve_user_llm(db, owner_id, server_client=server_client)
