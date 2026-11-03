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
from app.core.constants import DEFAULT_STRATEGY_VERSION
from app.db.models import AgentConfig, PolicySet, TradingAccount

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


@pytest.mark.asyncio
async def test_disconnect_succeeds_when_account_has_an_agent(client: AsyncClient) -> None:
    """Regression: disconnecting an account that an AgentConfig references must not 500.

    agent_configs.account_id is a NOT NULL FK, so hard-deleting the account row raised an
    IntegrityError (every user who ran a cycle has an agent). Disconnect now revokes the
    account instead: 204, the account reads as not-connected, the agent + history survive,
    and reconnecting reuses the same row.
    """
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    await client.post(
        "/accounts/alpaca",
        json={"api_key": API_KEY, "api_secret": API_SECRET},
        headers=headers,
    )

    # Attach an agent to the connected account (as POST /agent/cycles/run would).
    async with TestSessionLocal() as session:
        account = (await session.execute(select(TradingAccount))).scalar_one()
        policy = PolicySet(version="P-TEST", limits_json={}, is_active=True)
        session.add(policy)
        await session.flush()
        agent = AgentConfig(
            account_id=account.id,
            policy_set_id=policy.id,
            name="Test Agent",
            watchlist=["AAPL"],
            strategy_version=DEFAULT_STRATEGY_VERSION,
            model_provider="ollama",
            model_name="llama3.1",
        )
        session.add(agent)
        await session.commit()
        agent_id = agent.id

    # Disconnect must succeed, not raise a 500 FK violation.
    delete_response = await client.delete("/accounts/alpaca", headers=headers)
    assert delete_response.status_code == 204

    # Reads as not connected, but the agent (and its audit history) survive.
    status_response = await client.get("/accounts/alpaca", headers=headers)
    assert status_response.status_code == 404
    async with TestSessionLocal() as session:
        assert (await session.get(AgentConfig, agent_id)) is not None
        account = (await session.execute(select(TradingAccount))).scalar_one()
        assert account.is_connected is False

    # Reconnecting reuses the same row (no unique-constraint violation).
    reconnect = await client.post(
        "/accounts/alpaca",
        json={"api_key": API_KEY, "api_secret": API_SECRET},
        headers=headers,
    )
    assert reconnect.status_code == 201
    status_after = await client.get("/accounts/alpaca", headers=headers)
    assert status_after.status_code == 200


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
