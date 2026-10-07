"""Broker credential resolution (issue #7): the broker must use each user's
encrypted, vault-stored Alpaca credentials — decrypted only at call time — with a
dev-only plaintext fallback and no secret ever leaking to a client."""

import uuid
from unittest.mock import patch

import pytest
from conftest import TestSessionLocal
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import select

from app.accounts.credential_vault import CredentialVaultError
from app.broker.router import get_alpaca_client
from app.core.config import Settings
from app.db.models import User

API_KEY = "AKTEST_VAULT_KEY"
API_SECRET = "test-vault-secret-value"


async def _register_and_login(client: AsyncClient, email: str) -> str:
    password = "S3cure-Password!"
    await client.post("/auth/register", json={"email": email, "password": password})
    login = await client.post("/auth/login", json={"email": email, "password": password})
    return login.json()["access_token"]


async def _connect(
    client: AsyncClient, token: str, key: str = API_KEY, secret: str = API_SECRET
):
    return await client.post(
        "/accounts/alpaca",
        json={"api_key": key, "api_secret": secret},
        headers={"Authorization": f"Bearer {token}"},
    )


async def _fetch_user(email: str) -> User:
    async with TestSessionLocal() as session:
        return (await session.execute(select(User).where(User.email == email))).scalar_one()


async def _build_client(user, session):
    """Invoke the dependency directly, with the header overrides explicitly unset
    (FastAPI would inject None; a direct call must pass it)."""
    return await get_alpaca_client(
        current_user=user, db=session, x_alpaca_api_key=None, x_alpaca_api_secret=None
    )


# --- Endpoint-level auth / rejection ---


@pytest.mark.asyncio
async def test_broker_account_requires_auth(client: AsyncClient) -> None:
    response = await client.get("/api/v1/broker/account")
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_broker_rejects_unconnected_user_outside_dev(client: AsyncClient) -> None:
    token = await _register_and_login(client, f"test-{uuid.uuid4()}@example.com")

    # No account connected + non-development → 400, broker never called.
    with patch("app.broker.router.get_settings", return_value=Settings(app_env="production")):
        response = await client.get(
            "/api/v1/broker/account", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 400
    assert "connect" in response.json()["detail"].lower()


# --- Dependency-level credential resolution ---


@pytest.mark.asyncio
async def test_uses_decrypted_vault_credentials_when_connected(client: AsyncClient) -> None:
    email = f"test-{uuid.uuid4()}@example.com"
    token = await _register_and_login(client, email)
    assert (await _connect(client, token)).status_code == 201
    user = await _fetch_user(email)

    async with TestSessionLocal() as session:
        built = await _build_client(user, session)

    # The client must carry the ORIGINAL plaintext creds, proving they were
    # decrypted from the vault's ciphertext at call time.
    assert built.headers["APCA-API-KEY-ID"] == API_KEY
    assert built.headers["APCA-API-SECRET-KEY"] == API_SECRET
    await built.close()


@pytest.mark.asyncio
async def test_dev_fallback_uses_env_credentials_when_no_account(client: AsyncClient) -> None:
    email = f"test-{uuid.uuid4()}@example.com"
    await _register_and_login(client, email)  # registered but no account connected
    user = await _fetch_user(email)

    dev_settings = Settings(
        app_env="development",
        alpaca_api_key="ENV_FALLBACK_KEY",
        alpaca_api_secret="ENV_FALLBACK_SECRET",
    )
    async with TestSessionLocal() as session:
        with patch("app.broker.router.get_settings", return_value=dev_settings):
            built = await _build_client(user, session)

    assert built.headers["APCA-API-KEY-ID"] == "ENV_FALLBACK_KEY"
    await built.close()


@pytest.mark.asyncio
async def test_no_account_outside_dev_raises_400(client: AsyncClient) -> None:
    email = f"test-{uuid.uuid4()}@example.com"
    await _register_and_login(client, email)
    user = await _fetch_user(email)

    async with TestSessionLocal() as session:
        with patch("app.broker.router.get_settings", return_value=Settings(app_env="production")):
            with pytest.raises(HTTPException) as exc_info:
                await _build_client(user, session)

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_decrypt_failure_on_connected_account_raises_500(client: AsyncClient) -> None:
    email = f"test-{uuid.uuid4()}@example.com"
    token = await _register_and_login(client, email)
    await _connect(client, token)  # account exists in the DB
    user = await _fetch_user(email)

    async with TestSessionLocal() as session:
        with patch(
            "app.broker.router.get_decrypted_alpaca_credentials",
            side_effect=CredentialVaultError("corrupt token"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await _build_client(user, session)

    # Account present but undecryptable → fail loudly, never silently fall back.
    assert exc_info.value.status_code == 500
    assert API_SECRET not in str(exc_info.value.detail)
