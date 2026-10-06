import uuid
from unittest.mock import patch

import pytest
from conftest import TestSessionLocal
from httpx import AsyncClient
from sqlalchemy import select

from app.accounts.credential_vault import (
    CredentialVaultError,
    decrypt_credential,
    delete_alpaca_credentials,
    encrypt_credential,
    get_decrypted_alpaca_credentials,
)
from app.core.config import Settings
from app.db.models import TradingAccount

API_KEY = "AKFAKE1234567890"
API_SECRET = "s3cr3t-fake-alpaca-secret-value"


# --- Pure encrypt/decrypt (no DB) ---


def test_encrypt_decrypt_roundtrip():
    token = encrypt_credential(API_KEY)
    assert decrypt_credential(token) == API_KEY


def test_encrypted_value_does_not_contain_plaintext():
    token = encrypt_credential(API_SECRET)
    assert API_SECRET not in token
    assert token != API_SECRET


def test_decrypt_invalid_token_raises():
    with pytest.raises(CredentialVaultError):
        decrypt_credential("not-a-real-fernet-token")


def test_missing_encryption_key_fails_fast_with_clear_message():
    """No baked-in default — an unset key must raise a clear error, not AttributeError."""
    fake_settings = Settings(credential_encryption_key=None)
    with patch("app.accounts.credential_vault.get_settings", return_value=fake_settings):
        with pytest.raises(CredentialVaultError, match="CREDENTIAL_ENCRYPTION_KEY is not set"):
            encrypt_credential(API_KEY)


def test_malformed_encryption_key_fails_fast_with_clear_message():
    """An invalid (non-Fernet) key must raise a clear error, not a raw cryptography ValueError."""
    fake_settings = Settings(credential_encryption_key="not-a-valid-fernet-key")
    with patch("app.accounts.credential_vault.get_settings", return_value=fake_settings):
        with pytest.raises(CredentialVaultError, match="not a valid Fernet key"):
            encrypt_credential(API_KEY)


# --- Through the API, with direct DB verification ---


async def _register_and_login(client: AsyncClient) -> str:
    email = f"test-{uuid.uuid4()}@example.com"
    password = "S3cure-Password!"
    await client.post("/auth/register", json={"email": email, "password": password})
    login = await client.post("/auth/login", json={"email": email, "password": password})
    return login.json()["access_token"]


@pytest.mark.asyncio
async def test_connect_requires_auth(client: AsyncClient) -> None:
    response = await client.post(
        "/accounts/alpaca", json={"api_key": API_KEY, "api_secret": API_SECRET}
    )
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_connect_never_echoes_secrets_back(client: AsyncClient) -> None:
    token = await _register_and_login(client)
    response = await client.post(
        "/accounts/alpaca",
        json={"api_key": API_KEY, "api_secret": API_SECRET},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    body = response.json()
    assert "api_key" not in body
    assert "api_secret" not in body
    assert "encrypted_api_key" not in body
    assert API_KEY not in str(body)
    assert API_SECRET not in str(body)
    assert body["provider"] == "ALPACA"
    assert body["is_connected"] is True


@pytest.mark.asyncio
async def test_stored_value_is_encrypted_in_the_database(client: AsyncClient) -> None:
    token = await _register_and_login(client)
    await client.post(
        "/accounts/alpaca",
        json={"api_key": API_KEY, "api_secret": API_SECRET},
        headers={"Authorization": f"Bearer {token}"},
    )

    async with TestSessionLocal() as session:
        result = await session.execute(select(TradingAccount))
        account = result.scalar_one()

    # The raw DB column must never hold the plaintext secret.
    assert account.encrypted_api_key != API_KEY
    assert API_KEY not in account.encrypted_api_key
    assert account.encrypted_api_secret != API_SECRET
    assert API_SECRET not in account.encrypted_api_secret

    # But it must decrypt back to the original value.
    assert decrypt_credential(account.encrypted_api_key) == API_KEY
    assert decrypt_credential(account.encrypted_api_secret) == API_SECRET


@pytest.mark.asyncio
async def test_reconnecting_updates_rather_than_duplicates(client: AsyncClient) -> None:
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    await client.post(
        "/accounts/alpaca",
        json={"api_key": API_KEY, "api_secret": API_SECRET},
        headers=headers,
    )
    await client.post(
        "/accounts/alpaca",
        json={"api_key": "NEW_KEY", "api_secret": "NEW_SECRET"},
        headers=headers,
    )

    async with TestSessionLocal() as session:
        result = await session.execute(select(TradingAccount))
        accounts = result.scalars().all()

    assert len(accounts) == 1
    assert decrypt_credential(accounts[0].encrypted_api_key) == "NEW_KEY"


@pytest.mark.asyncio
async def test_get_status_404_when_not_connected(client: AsyncClient) -> None:
    token = await _register_and_login(client)
    response = await client.get(
        "/accounts/alpaca", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_status_after_connect(client: AsyncClient) -> None:
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    await client.post(
        "/accounts/alpaca",
        json={"api_key": API_KEY, "api_secret": API_SECRET},
        headers=headers,
    )
    response = await client.get("/accounts/alpaca", headers=headers)
    assert response.status_code == 200
    assert response.json()["is_connected"] is True


@pytest.mark.asyncio
async def test_disconnect_removes_the_account(client: AsyncClient) -> None:
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    await client.post(
        "/accounts/alpaca",
        json={"api_key": API_KEY, "api_secret": API_SECRET},
        headers=headers,
    )

    delete_response = await client.delete("/accounts/alpaca", headers=headers)
    assert delete_response.status_code == 204

    status_response = await client.get("/accounts/alpaca", headers=headers)
    assert status_response.status_code == 404


@pytest.mark.asyncio
async def test_disconnect_404_when_nothing_connected(client: AsyncClient) -> None:
    token = await _register_and_login(client)
    response = await client.delete(
        "/accounts/alpaca", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 404


# --- Runtime decryption helper (what a future broker integration would call) ---


@pytest.mark.asyncio
async def test_get_decrypted_credentials_raises_when_not_connected(client: AsyncClient) -> None:
    async with TestSessionLocal() as session:
        with pytest.raises(CredentialVaultError):
            await get_decrypted_alpaca_credentials(session, uuid.uuid4())


@pytest.mark.asyncio
async def test_delete_returns_false_when_nothing_to_delete(client: AsyncClient) -> None:
    async with TestSessionLocal() as session:
        deleted = await delete_alpaca_credentials(session, uuid.uuid4())
    assert deleted is False
