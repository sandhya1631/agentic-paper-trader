"""Tests for GET /agent/cycles - paginated, user-scoped cycle list (#119)."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from conftest import TestSessionLocal
from httpx import AsyncClient
from sqlalchemy import select

from app.db.enums import CycleStatus, CycleTrigger
from app.db.models import AgentConfig, AgentCycle, PolicySet, TradingAccount, User


async def _register_and_login(client: AsyncClient, email: str) -> str:
    password = "S3cure-Password!"
    await client.post("/auth/register", json={"email": email, "password": password})
    login = await client.post("/auth/login", json={"email": email, "password": password})
    return login.json()["access_token"]


async def _create_agent_for(db, email: str) -> AgentConfig:
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one()
    account = TradingAccount(
        user_id=user.id,
        provider="ALPACA",
        encrypted_api_key="enc_key",
        encrypted_api_secret="enc_secret",
        is_paper=True,
    )
    db.add(account)
    await db.flush()
    policy_set = PolicySet(version=f"P-{uuid.uuid4()}", limits_json={}, is_active=True)
    db.add(policy_set)
    await db.flush()
    agent = AgentConfig(
        account_id=account.id,
        policy_set_id=policy_set.id,
        name="List Test Agent",
        watchlist=["AAPL"],
        strategy_version="S-001",
        enabled=True,
        model_provider="ollama",
        model_name="llama3.1",
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return agent


def _cycle(
    agent_id: uuid.UUID,
    *,
    minutes_ago: int,
    trigger: CycleTrigger = CycleTrigger.MANUAL,
    status: CycleStatus = CycleStatus.HOLD,
    action: str | None = None,
    policy_decision: str | None = None,
) -> AgentCycle:
    started = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return AgentCycle(
        agent_id=agent_id,
        trigger=trigger,
        status=status,
        started_at=started,
        proposal_json=(
            {"symbol": "AAPL", "action": action, "quantity": 1, "reasoning": "x"}
            if action
            else None
        ),
        policy_decision=policy_decision,
    )


@pytest.mark.asyncio
async def test_agent_cycles_list_requires_auth(client: AsyncClient):
    """Unauthenticated request is rejected with 401."""
    resp = await client.get("/agent/cycles")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_agent_cycles_list_scoped_to_user(client: AsyncClient):
    """A caller sees only their own agents' cycles, never another user's."""
    token_a = await _register_and_login(client, "owner-a@example.com")
    await _register_and_login(client, "owner-b@example.com")
    async with TestSessionLocal() as db:
        agent_a = await _create_agent_for(db, "owner-a@example.com")
        agent_b = await _create_agent_for(db, "owner-b@example.com")
        db.add_all(
            [
                _cycle(agent_a.id, minutes_ago=1),
                _cycle(agent_a.id, minutes_ago=2),
            ]
        )
        db.add(_cycle(agent_b.id, minutes_ago=1))
        await db.commit()
        a_agent_id = agent_a.id

    resp = await client.get(
        "/agent/cycles", headers={"Authorization": f"Bearer {token_a}"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    assert {item["agent_id"] for item in body["items"]} == {str(a_agent_id)}


@pytest.mark.asyncio
async def test_agent_cycles_list_ordering_and_triggers(client: AsyncClient):
    """Newest-first by started_at; both MANUAL and SCHEDULED cycles are included."""
    token = await _register_and_login(client, "order@example.com")
    async with TestSessionLocal() as db:
        agent = await _create_agent_for(db, "order@example.com")
        db.add_all(
            [
                _cycle(agent.id, minutes_ago=30, trigger=CycleTrigger.SCHEDULED),
                _cycle(agent.id, minutes_ago=10, trigger=CycleTrigger.MANUAL),
                _cycle(agent.id, minutes_ago=20, trigger=CycleTrigger.SCHEDULED),
            ]
        )
        await db.commit()

    resp = await client.get(
        "/agent/cycles", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    body = resp.json()
    started = [item["started_at"] for item in body["items"]]
    assert started == sorted(started, reverse=True)  # newest first
    assert {item["trigger"] for item in body["items"]} == {"MANUAL", "SCHEDULED"}


@pytest.mark.asyncio
async def test_agent_cycles_list_pagination(client: AsyncClient):
    """limit/offset boundaries: page size respected, total/next correct, no overlap/gaps."""
    token = await _register_and_login(client, "page@example.com")
    async with TestSessionLocal() as db:
        agent = await _create_agent_for(db, "page@example.com")
        db.add_all([_cycle(agent.id, minutes_ago=i) for i in range(1, 6)])  # 5 cycles
        await db.commit()

    headers = {"Authorization": f"Bearer {token}"}
    seen: list[str] = []

    r1 = (await client.get("/agent/cycles?limit=2&offset=0", headers=headers)).json()
    assert r1["total"] == 5
    assert len(r1["items"]) == 2
    assert r1["next_offset"] == 2
    seen += [item["id"] for item in r1["items"]]

    r2 = (await client.get("/agent/cycles?limit=2&offset=2", headers=headers)).json()
    assert len(r2["items"]) == 2
    assert r2["next_offset"] == 4
    seen += [item["id"] for item in r2["items"]]

    r3 = (await client.get("/agent/cycles?limit=2&offset=4", headers=headers)).json()
    assert len(r3["items"]) == 1
    assert r3["next_offset"] is None
    seen += [item["id"] for item in r3["items"]]

    # Every row appears exactly once across the three pages (no overlap, no gaps).
    assert len(seen) == 5
    assert len(set(seen)) == 5


@pytest.mark.asyncio
async def test_agent_cycles_list_rejects_out_of_range_limit(client: AsyncClient):
    """limit must be within 1..100."""
    token = await _register_and_login(client, "bounds@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/agent/cycles?limit=0", headers=headers)).status_code == 422
    assert (await client.get("/agent/cycles?limit=101", headers=headers)).status_code == 422


@pytest.mark.asyncio
async def test_agent_cycles_list_filters_by_action(client: AsyncClient):
    """#82: filter by the LLM's proposed action."""
    token = await _register_and_login(client, "filter-action@example.com")
    async with TestSessionLocal() as db:
        agent = await _create_agent_for(db, "filter-action@example.com")
        db.add_all(
            [
                _cycle(agent.id, minutes_ago=1, action="BUY"),
                _cycle(agent.id, minutes_ago=2, action="SELL"),
                _cycle(agent.id, minutes_ago=3, action="HOLD"),
            ]
        )
        await db.commit()

    headers = {"Authorization": f"Bearer {token}"}
    resp = await client.get("/agent/cycles?action=BUY", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["proposal_json"]["action"] == "BUY"


@pytest.mark.asyncio
async def test_agent_cycles_list_filters_by_status(client: AsyncClient):
    """#82: filter by cycle status."""
    token = await _register_and_login(client, "filter-status@example.com")
    async with TestSessionLocal() as db:
        agent = await _create_agent_for(db, "filter-status@example.com")
        db.add_all(
            [
                _cycle(agent.id, minutes_ago=1, status=CycleStatus.COMPLETED),
                _cycle(agent.id, minutes_ago=2, status=CycleStatus.FAILED),
            ]
        )
        await db.commit()

    headers = {"Authorization": f"Bearer {token}"}
    resp = await client.get("/agent/cycles?status=FAILED", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["status"] == "FAILED"


@pytest.mark.asyncio
async def test_agent_cycles_list_filters_by_policy_decision(client: AsyncClient):
    """#82: filter by the cycle's overall policy outcome."""
    token = await _register_and_login(client, "filter-policy@example.com")
    async with TestSessionLocal() as db:
        agent = await _create_agent_for(db, "filter-policy@example.com")
        db.add_all(
            [
                _cycle(agent.id, minutes_ago=1, policy_decision="DENY"),
                _cycle(agent.id, minutes_ago=2, policy_decision="ALLOW"),
                _cycle(agent.id, minutes_ago=3, policy_decision=None),
            ]
        )
        await db.commit()

    headers = {"Authorization": f"Bearer {token}"}
    resp = await client.get("/agent/cycles?policy_decision=DENY", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["policy_decision"] == "DENY"


@pytest.mark.asyncio
async def test_agent_cycles_list_filters_by_date_range(client: AsyncClient):
    """#82: started_after/started_before, inclusive, AND together."""
    token = await _register_and_login(client, "filter-date@example.com")
    async with TestSessionLocal() as db:
        agent = await _create_agent_for(db, "filter-date@example.com")
        db.add_all(
            [
                _cycle(agent.id, minutes_ago=5),
                _cycle(agent.id, minutes_ago=50),
                _cycle(agent.id, minutes_ago=500),
            ]
        )
        await db.commit()

    headers = {"Authorization": f"Bearer {token}"}
    lower = (datetime.now(timezone.utc) - timedelta(minutes=100)).isoformat()
    upper = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    resp = await client.get(
        "/agent/cycles",
        params={"started_after": lower, "started_before": upper},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1  # only the 50-minutes-ago cycle is in range


@pytest.mark.asyncio
async def test_agent_cycles_list_combines_filters_with_and(client: AsyncClient):
    """#82: multiple filters AND together, not OR."""
    token = await _register_and_login(client, "filter-and@example.com")
    async with TestSessionLocal() as db:
        agent = await _create_agent_for(db, "filter-and@example.com")
        db.add_all(
            [
                _cycle(agent.id, minutes_ago=1, action="BUY", status=CycleStatus.FAILED),
                _cycle(agent.id, minutes_ago=2, action="BUY", status=CycleStatus.COMPLETED),
                _cycle(agent.id, minutes_ago=3, action="SELL", status=CycleStatus.FAILED),
            ]
        )
        await db.commit()

    headers = {"Authorization": f"Bearer {token}"}
    resp = await client.get("/agent/cycles?action=BUY&status=FAILED", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["proposal_json"]["action"] == "BUY"
    assert body["items"][0]["status"] == "FAILED"


@pytest.mark.asyncio
async def test_agent_cycles_list_filters_scoped_to_user_too(client: AsyncClient):
    """A filter must not leak another user's matching cycles into the result."""
    token_a = await _register_and_login(client, "filter-scope-a@example.com")
    await _register_and_login(client, "filter-scope-b@example.com")
    async with TestSessionLocal() as db:
        agent_a = await _create_agent_for(db, "filter-scope-a@example.com")
        agent_b = await _create_agent_for(db, "filter-scope-b@example.com")
        db.add(_cycle(agent_a.id, minutes_ago=1, action="BUY"))
        db.add(_cycle(agent_b.id, minutes_ago=1, action="BUY"))
        await db.commit()

    resp = await client.get(
        "/agent/cycles?action=BUY", headers={"Authorization": f"Bearer {token_a}"}
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


@pytest.mark.asyncio
async def test_agent_cycles_list_rejects_inverted_date_range(client: AsyncClient):
    """started_after after started_before is a clean 422, not an empty/wrong result."""
    token = await _register_and_login(client, "filter-bad-range@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    later = datetime.now(timezone.utc).isoformat()
    earlier = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    resp = await client.get(
        "/agent/cycles",
        params={"started_after": later, "started_before": earlier},
        headers=headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_agent_cycles_list_rejects_invalid_enum_filters(client: AsyncClient):
    """An unknown action/status/policy_decision value is a 422, not a 500 or silent no-op."""
    token = await _register_and_login(client, "filter-bad-enum@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/agent/cycles?action=NOPE", headers=headers)).status_code == 422
    assert (await client.get("/agent/cycles?status=NOPE", headers=headers)).status_code == 422
    assert (
        await client.get("/agent/cycles?policy_decision=NOPE", headers=headers)
    ).status_code == 422
