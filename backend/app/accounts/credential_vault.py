"""Encrypted Alpaca Credential Vault (story 0.2.3 / issue #19).

Alpaca API keys are symmetrically encrypted (Fernet) before they ever touch the
database, and decrypted only at the point of use for an actual broker call —
the plaintext never reaches the frontend, logs, or the LLM. Per the Core
Controls Matrix: "never send secrets to frontend or model."

Revocation is implemented (DELETE /accounts/alpaca). Key rotation (re-encrypting
stored credentials under a new Fernet key, e.g. via MultiFernet) is not — this
module assumes a single active key for now.
"""

import uuid

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import TradingAccount

ALPACA_PROVIDER = "ALPACA"


class CredentialVaultError(Exception):
    """Raised when credentials cannot be encrypted, decrypted, or found."""


def _fernet() -> Fernet:
    settings = get_settings()
    if not settings.credential_encryption_key:
        raise CredentialVaultError(
            "CREDENTIAL_ENCRYPTION_KEY is not set. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; "
            'print(Fernet.generate_key().decode())"'
        )
    try:
        return Fernet(settings.credential_encryption_key.encode("utf-8"))
    except ValueError as exc:
        raise CredentialVaultError(
            "CREDENTIAL_ENCRYPTION_KEY is not a valid Fernet key "
            "(must be 32 url-safe base64-encoded bytes)"
        ) from exc


def encrypt_credential(plaintext: str) -> str:
    """Encrypt a single secret (API key or API secret) for storage."""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_credential(token: str) -> str:
    """Decrypt a stored secret. Raises CredentialVaultError on a corrupt/invalid token."""
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise CredentialVaultError(
            "Stored credential is invalid or was encrypted with a different key"
        ) from exc


async def get_alpaca_account(db: AsyncSession, user_id: uuid.UUID) -> TradingAccount | None:
    """Return the user's TradingAccount row (fields still encrypted), or None if not connected."""
    result = await db.execute(
        select(TradingAccount).where(
            TradingAccount.user_id == user_id, TradingAccount.provider == ALPACA_PROVIDER
        )
    )
    return result.scalar_one_or_none()


async def save_alpaca_credentials(
    db: AsyncSession,
    user_id: uuid.UUID,
    api_key: str,
    api_secret: str,
    *,
    is_paper: bool = True,
) -> TradingAccount:
    """Encrypt and store (or update, if already connected) a user's Alpaca credentials."""
    account = await get_alpaca_account(db, user_id)
    encrypted_key = encrypt_credential(api_key)
    encrypted_secret = encrypt_credential(api_secret)

    if account is None:
        account = TradingAccount(
            user_id=user_id,
            provider=ALPACA_PROVIDER,
            encrypted_api_key=encrypted_key,
            encrypted_api_secret=encrypted_secret,
            is_paper=is_paper,
            is_connected=True,
        )
        db.add(account)
    else:
        account.encrypted_api_key = encrypted_key
        account.encrypted_api_secret = encrypted_secret
        account.is_paper = is_paper
        account.is_connected = True

    await db.commit()
    await db.refresh(account)
    return account


async def get_decrypted_alpaca_credentials(
    db: AsyncSession, user_id: uuid.UUID
) -> tuple[str, str]:
    """Runtime decryption for broker calls. Never log or return this tuple to a client."""
    account = await get_alpaca_account(db, user_id)
    if account is None:
        raise CredentialVaultError("No Alpaca credentials are connected for this user")
    return (
        decrypt_credential(account.encrypted_api_key),
        decrypt_credential(account.encrypted_api_secret),
    )


async def delete_alpaca_credentials(db: AsyncSession, user_id: uuid.UUID) -> bool:
    """Revoke/remove a user's stored Alpaca credentials. Returns True if something was deleted."""
    account = await get_alpaca_account(db, user_id)
    if account is None:
        return False
    await db.delete(account)
    await db.commit()
    return True
