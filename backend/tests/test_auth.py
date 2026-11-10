import logging
import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import TestSessionLocal
from httpx import AsyncClient
from sqlalchemy import select

from app.auth.password_reset import hash_reset_token
from app.db.models import PasswordResetToken, User


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
    # /auth/me exposes the role so the frontend can gate admin-only controls.
    assert authenticated.json()["role"] == "VIEWER"


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


@pytest.mark.asyncio
async def test_inactive_user_cannot_use_an_existing_token(client: AsyncClient) -> None:
    email = f"inactive-{uuid.uuid4()}@example.com"
    password = "S3cure-Password!"
    await client.post("/auth/register", json={"email": email, "password": password})
    token = (
        await client.post(
            "/auth/login", json={"email": email, "password": password}
        )
    ).json()["access_token"]

    async with TestSessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one()
        user.is_active = False
        await db.commit()

    response = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_viewer_cannot_submit_direct_orders(client: AsyncClient) -> None:
    email = f"viewer-{uuid.uuid4()}@example.com"
    password = "S3cure-Password!"
    await client.post("/auth/register", json={"email": email, "password": password})
    token = (
        await client.post(
            "/auth/login", json={"email": email, "password": password}
        )
    ).json()["access_token"]

    response = await client.post(
        "/api/v1/broker/orders",
        json={
            "symbol": "AAPL",
            "qty": 1,
            "side": "BUY",
            "client_order_id": "viewer-order",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_register_rejects_overlong_password(client: AsyncClient) -> None:
    # >72 bytes would make bcrypt raise; it must be a clean validation error, not a 500.
    email = f"test-{uuid.uuid4()}@example.com"
    response = await client.post(
        "/auth/register", json={"email": email, "password": "a" * 73}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_register_rejects_too_short_password(client: AsyncClient) -> None:
    email = f"test-{uuid.uuid4()}@example.com"
    response = await client.post(
        "/auth/register", json={"email": email, "password": "short"}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_login_rejects_overlong_password(client: AsyncClient) -> None:
    # bcrypt.checkpw() raises on >72 bytes; login must reject cleanly (422), not 500.
    email = f"test-{uuid.uuid4()}@example.com"
    response = await client.post(
        "/auth/login", json={"email": email, "password": "a" * 73}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_oauth2_form_login_and_protected_route(client: AsyncClient) -> None:
    email = f"test-{uuid.uuid4()}@example.com"
    password = "S3cure-Password!"

    register_response = await client.post(
        "/auth/register", json={"email": email, "password": password}
    )
    assert register_response.status_code == 201

    token_response = await client.post(
        "/auth/token", data={"username": email, "password": password}
    )
    assert token_response.status_code == 200
    token = token_response.json()["access_token"]
    assert token is not None

    authenticated = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert authenticated.status_code == 200
    assert authenticated.json()["email"] == email


@pytest.mark.asyncio
async def test_oauth2_token_rejects_overlong_password(client: AsyncClient) -> None:
    # bcrypt.checkpw() raises on >72 bytes; OAuth2 form login must reject cleanly (422), not 500.
    email = f"test-{uuid.uuid4()}@example.com"
    response = await client.post(
        "/auth/token", data={"username": email, "password": "a" * 73}
    )
    assert response.status_code == 422


def _extract_reset_token(caplog: pytest.LogCaptureFixture) -> str:
    """Dev-mode "email" is a server log line; pull the token out of its reset link."""
    for record in caplog.records:
        match = re.search(r"token=(\S+)", record.message)
        if match:
            return match.group(1)
    raise AssertionError("No password reset link was logged")


@pytest.mark.asyncio
async def test_forgot_password_logs_a_usable_reset_link(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    email = f"test-{uuid.uuid4()}@example.com"
    old_password = "S3cure-Password!"
    new_password = "N3w-Password!!"
    await client.post("/auth/register", json={"email": email, "password": old_password})

    with caplog.at_level(logging.WARNING, logger="app.auth.password_reset"):
        response = await client.post("/auth/forgot-password", json={"email": email})
    assert response.status_code == 200
    generic_message = response.json()["detail"]
    token = _extract_reset_token(caplog)

    reset_response = await client.post(
        "/auth/reset-password", json={"token": token, "new_password": new_password}
    )
    assert reset_response.status_code == 200

    # Old password no longer works; the new one does.
    old_login = await client.post(
        "/auth/login", json={"email": email, "password": old_password}
    )
    assert old_login.status_code == 401
    new_login = await client.post(
        "/auth/login", json={"email": email, "password": new_password}
    )
    assert new_login.status_code == 200

    # The forgot-password response must not reveal whether the email existed.
    unknown_response = await client.post(
        "/auth/forgot-password", json={"email": f"nobody-{uuid.uuid4()}@example.com"}
    )
    assert unknown_response.status_code == 200
    assert unknown_response.json()["detail"] == generic_message


@pytest.mark.asyncio
async def test_forgot_password_for_unknown_email_sends_no_link(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="app.auth.password_reset"):
        response = await client.post(
            "/auth/forgot-password", json={"email": f"nobody-{uuid.uuid4()}@example.com"}
        )
    assert response.status_code == 200
    assert not caplog.records  # nothing logged: no account to send a link for


@pytest.mark.asyncio
async def test_reset_password_rejects_garbage_token(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/reset-password",
        json={"token": "not-a-real-token", "new_password": "N3w-Password!!"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_reset_password_token_is_single_use(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    email = f"test-{uuid.uuid4()}@example.com"
    await client.post(
        "/auth/register", json={"email": email, "password": "S3cure-Password!"}
    )
    with caplog.at_level(logging.WARNING, logger="app.auth.password_reset"):
        await client.post("/auth/forgot-password", json={"email": email})
    token = _extract_reset_token(caplog)

    first = await client.post(
        "/auth/reset-password", json={"token": token, "new_password": "N3w-Password!!"}
    )
    assert first.status_code == 200

    second = await client.post(
        "/auth/reset-password", json={"token": token, "new_password": "Another-Pass!!"}
    )
    assert second.status_code == 400


@pytest.mark.asyncio
async def test_reset_password_rejects_an_expired_token(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    email = f"test-{uuid.uuid4()}@example.com"
    await client.post(
        "/auth/register", json={"email": email, "password": "S3cure-Password!"}
    )
    with caplog.at_level(logging.WARNING, logger="app.auth.password_reset"):
        await client.post("/auth/forgot-password", json={"email": email})
    token = _extract_reset_token(caplog)

    async with TestSessionLocal() as db:
        result = await db.execute(
            select(PasswordResetToken).where(
                PasswordResetToken.token_hash == hash_reset_token(token)
            )
        )
        reset_token = result.scalar_one()
        reset_token.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await db.commit()

    response = await client.post(
        "/auth/reset-password", json={"token": token, "new_password": "N3w-Password!!"}
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_second_forgot_password_request_invalidates_the_first_link(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    email = f"test-{uuid.uuid4()}@example.com"
    await client.post(
        "/auth/register", json={"email": email, "password": "S3cure-Password!"}
    )

    with caplog.at_level(logging.WARNING, logger="app.auth.password_reset"):
        await client.post("/auth/forgot-password", json={"email": email})
    first_token = _extract_reset_token(caplog)
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger="app.auth.password_reset"):
        await client.post("/auth/forgot-password", json={"email": email})
    second_token = _extract_reset_token(caplog)

    stale_attempt = await client.post(
        "/auth/reset-password", json={"token": first_token, "new_password": "N3w-Password!!"}
    )
    assert stale_attempt.status_code == 400

    fresh_attempt = await client.post(
        "/auth/reset-password", json={"token": second_token, "new_password": "N3w-Password!!"}
    )
    assert fresh_attempt.status_code == 200
