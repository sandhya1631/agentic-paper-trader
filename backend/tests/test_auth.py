import uuid

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_register_login_and_protected_route(client: AsyncClient) -> None:
    email = f"test-{uuid.uuid4()}@example.com"
    password = "S3cure-Password!"

    register_response = await client.post(
        "/auth/register", json={"email": email, "password": password}
    )
    assert register_response.status_code == 201
    assert register_response.json()["email"] == email

    login_response = await client.post(
        "/auth/login", json={"email": email, "password": password}
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    unauthenticated = await client.get("/auth/me")
    assert unauthenticated.status_code in (401, 403)

    authenticated = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert authenticated.status_code == 200
    assert authenticated.json()["email"] == email


@pytest.mark.asyncio
async def test_login_with_wrong_password_is_rejected(client: AsyncClient) -> None:
    email = f"test-{uuid.uuid4()}@example.com"
    password = "S3cure-Password!"

    await client.post("/auth/register", json={"email": email, "password": password})

    response = await client.post(
        "/auth/login", json={"email": email, "password": "wrong-password"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_invalid_token_is_rejected(client: AsyncClient) -> None:
    response = await client.get(
        "/auth/me", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert response.status_code == 401
