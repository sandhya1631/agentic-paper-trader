"""GET/PATCH /agent/config - owner-scoped agent configuration (#141, supersedes #138).

Locks in: the owner can read the config; an OPERATOR/ADMIN can change only the
watchlist (normalized, de-duplicated, capped) and approval mode; every other field
(cadence, risk limits, ...) is rejected with 422 and nothing persists; no agent means
a 404 and nothing is provisioned; another user's agent is a 404; each real change
writes exactly one audit event in the same commit; and the next cycle (manual or
scheduled) reads the updated config.
"""

import json
import uuid
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from conftest import TestSessionLocal
from httpx import AsyncClient
from sqlalchemy import func, select

from app.agent.orchestrator import run_watchlist_cycles
from app.core.constants import DEFAULT_WATCHLIST, MAX_WATCHLIST_SIZE
from app.db.enums import ApprovalMode, CycleStatus, CycleTrigger, UserRole
from app.db.models import (
    AgentConfig,
    AuditEvent,
    PolicySet,
    TradingAccount,
    User,
)
from app.market_data.alpaca_client import OHLCVBar
from app.schemas import AuditEventRead

PASSWORD = "S3cure-Password!"
NO_AGENT_DETAIL = "No agent configured yet. Trigger a cycle to create your default agent."
CONFIG_FIELDS = {
    "id",
    "name",
    "strategy_version",
    "cadence_minutes",
    "target_allocation",
    "max_symbol_allocation",
    "cash_reserve",
    "daily_loss_limit",
    "approval_mode",
    "enabled",
    "watchlist",
}
REASONING = "RSI-14 is 30.0 (oversold). Citing RSI=30.0, SMA20=1.00."


async def _register_and_login(client: AsyncClient, email: str) -> str:
    await client.post("/auth/register", json={"email": email, "password": PASSWORD})
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    return login.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _set_role(email: str, role: UserRole) -> None:
    # The token keeps working: get_current_user reloads the user on every request.
    async with TestSessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one()
        user.role = role
        await db.commit()


async def _operator(client: AsyncClient, email: str) -> str:
    token = await _register_and_login(client, email)
    await _set_role(email, UserRole.OPERATOR)
    return token


async def _seed_agent_for(
    email: str,
    *,
    watchlist: list[str] | None = None,
    approval_mode: ApprovalMode = ApprovalMode.REQUIRED,
) -> uuid.UUID:
    async with TestSessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one()
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
            name="Config Test Agent",
            watchlist=list(watchlist if watchlist is not None else DEFAULT_WATCHLIST),
            strategy_version="S-001",
            approval_mode=approval_mode,
            enabled=True,
            model_provider="ollama",
            model_name="llama3.1",
        )
        db.add(agent)
        await db.commit()
        return agent.id


async def _load_agent(agent_id: uuid.UUID) -> AgentConfig:
    async with TestSessionLocal() as db:
        return (
            await db.execute(select(AgentConfig).where(AgentConfig.id == agent_id))
        ).scalar_one()


async def _audit_events() -> list[AuditEvent]:
    async with TestSessionLocal() as db:
        result = await db.execute(select(AuditEvent).order_by(AuditEvent.occurred_at))
        return list(result.scalars().all())


def _make_bar(price: float) -> OHLCVBar:
    return OHLCVBar(
        timestamp="2026-10-05T14:00:00+00:00",
        open=price,
        high=price + 1.0,
        low=price - 1.0,
        close=price,
        volume=1000.0,
    )


def _collector_for(symbols: list[str]) -> MagicMock:
    collector = MagicMock()
    collector.fetch_latest_bars = AsyncMock(
        return_value={s: [_make_bar(150.0) for _ in range(50)] for s in symbols}
    )
    return collector


def _llm(action: str, symbol: str | None = None) -> MagicMock:
    """A model that answers `action` for the cycle's decision_target (or `symbol`)."""

    def gen(prompt_input: str, system: str | None = None) -> str:
        target = symbol or json.loads(prompt_input).get("decision_target")
        proposal: dict[str, Any] = {"symbol": target, "action": action, "reasoning": REASONING}
        if action != "HOLD":
            proposal["quantity"] = 1
        return json.dumps(proposal)

    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=gen)
    return llm


@pytest.mark.asyncio
async def test_agent_config_requires_auth(client: AsyncClient):
    assert (await client.get("/agent/config")).status_code == 401
    response = await client.patch("/agent/config", json={"approval_mode": "AUTO"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_agent_config_returns_fields(client: AsyncClient):
    """The owner sees watchlist / approval_mode / cadence_minutes plus read-only limits.

    A VIEWER (the default role) may read; only PATCH is operator-gated.
    """
    email = "cfg-get@example.com"
    token = await _register_and_login(client, email)
    agent_id = await _seed_agent_for(email)

    response = await client.get("/agent/config", headers=_auth(token))

    assert response.status_code == 200
    body = response.json()
    assert set(body) == CONFIG_FIELDS
    assert body["id"] == str(agent_id)
    assert body["name"] == "Config Test Agent"
    assert body["watchlist"] == ["AAPL", "NVDA", "SPY"]
    assert body["approval_mode"] == "REQUIRED"
    assert body["cadence_minutes"] == 15
    assert body["strategy_version"] == "S-001"
    assert body["enabled"] is True
    assert Decimal(body["target_allocation"]) == Decimal("0.05")
    assert Decimal(body["max_symbol_allocation"]) == Decimal("0.10")
    assert Decimal(body["cash_reserve"]) == Decimal("0.05")
    assert Decimal(body["daily_loss_limit"]) == Decimal("0.02")

    # The same agent is addressable explicitly.
    explicit = await client.get(f"/agent/config?agent_id={agent_id}", headers=_auth(token))
    assert explicit.status_code == 200
    assert explicit.json() == body


@pytest.mark.asyncio
async def test_get_agent_config_no_agent_returns_404(client: AsyncClient):
    """No agent -> clear 404 on GET and PATCH; nothing is auto-provisioned."""
    email = "cfg-none@example.com"
    token = await _register_and_login(client, email)

    get_response = await client.get("/agent/config", headers=_auth(token))
    assert get_response.status_code == 404
    assert get_response.json()["detail"] == NO_AGENT_DETAIL

    await _set_role(email, UserRole.OPERATOR)
    patch_response = await client.patch(
        "/agent/config", json={"approval_mode": "AUTO"}, headers=_auth(token)
    )
    assert patch_response.status_code == 404
    assert patch_response.json()["detail"] == NO_AGENT_DETAIL

    async with TestSessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(TradingAccount)) == 0
        assert await db.scalar(select(func.count()).select_from(AgentConfig)) == 0
        assert await db.scalar(select(func.count()).select_from(PolicySet)) == 0
        assert await db.scalar(select(func.count()).select_from(AuditEvent)) == 0
    # No placeholder account appeared, so Settings still reads "not connected".
    assert (await client.get("/accounts/alpaca", headers=_auth(token))).status_code == 404


@pytest.mark.asyncio
async def test_patch_watchlist_persists_and_normalizes(client: AsyncClient):
    email = "cfg-norm@example.com"
    token = await _operator(client, email)
    agent_id = await _seed_agent_for(email)

    response = await client.patch(
        "/agent/config",
        json={"watchlist": ["aapl", " nvda ", "AAPL"]},
        headers=_auth(token),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["watchlist"] == ["AAPL", "NVDA"]
    assert body["approval_mode"] == "REQUIRED"  # untouched
    assert body["cadence_minutes"] == 15

    reread = await client.get("/agent/config", headers=_auth(token))
    assert reread.json()["watchlist"] == ["AAPL", "NVDA"]

    agent = await _load_agent(agent_id)
    assert agent.watchlist == ["AAPL", "NVDA"]
    assert agent.version == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"watchlist": []}, id="empty-watchlist"),
        pytest.param({"watchlist": ["BAD SYMBOL"]}, id="symbol-with-space"),
        pytest.param({"watchlist": ["1ABC"]}, id="symbol-leading-digit"),
        pytest.param({"watchlist": ["ABCDEFGHIJK"]}, id="symbol-too-long"),
        pytest.param({"watchlist": [""]}, id="empty-symbol"),
        pytest.param(
            {"watchlist": [f"S{i}" for i in range(MAX_WATCHLIST_SIZE + 1)]},
            id="too-many-symbols",
        ),
        pytest.param({"watchlist": [123]}, id="non-string-symbol"),
        pytest.param({"watchlist": "AAPL"}, id="watchlist-not-a-list"),
        pytest.param({"approval_mode": "YOLO"}, id="invalid-approval-mode"),
        pytest.param({"cadence_minutes": 30}, id="cadence"),
        pytest.param({}, id="empty-body"),
        pytest.param({"max_symbol_allocation": "0.5"}, id="risk-limit"),
        pytest.param({"watchlist": ["MSFT"], "daily_loss_limit": "0.5"}, id="valid-plus-forbidden"),
        pytest.param({"watchlist": None}, id="null-watchlist"),
        pytest.param({"approval_mode": None}, id="null-approval-mode"),
    ],
)
async def test_patch_rejects_invalid(client: AsyncClient, payload: dict[str, Any]):
    email = "cfg-invalid@example.com"
    token = await _operator(client, email)
    agent_id = await _seed_agent_for(email)
    before = (await client.get("/agent/config", headers=_auth(token))).json()

    response = await client.patch("/agent/config", json=payload, headers=_auth(token))

    assert response.status_code == 422
    after = (await client.get("/agent/config", headers=_auth(token))).json()
    assert after == before
    agent = await _load_agent(agent_id)
    assert agent.watchlist == ["AAPL", "NVDA", "SPY"]
    assert agent.approval_mode == ApprovalMode.REQUIRED
    assert agent.max_symbol_allocation == Decimal("0.10")
    assert agent.daily_loss_limit == Decimal("0.02")
    assert agent.version == 1
    assert await _audit_events() == []


@pytest.mark.asyncio
async def test_patch_cadence_is_rejected(client: AsyncClient):
    """cadence_minutes is display-only: the run interval is the Autonomous-schedule
    interval (POST /agent/scheduler/start), so PATCH rejects cadence outright."""
    email = "cfg-cadence@example.com"
    token = await _operator(client, email)
    agent_id = await _seed_agent_for(email)

    for cadence in (30, 15):
        response = await client.patch(
            "/agent/config", json={"cadence_minutes": cadence}, headers=_auth(token)
        )
        assert response.status_code == 422
        assert any(
            error["loc"][-1] == "cadence_minutes" for error in response.json()["detail"]
        )

    assert (await client.get("/agent/config", headers=_auth(token))).json()[
        "cadence_minutes"
    ] == 15
    assert (await _load_agent(agent_id)).cadence_minutes == 15


@pytest.mark.asyncio
async def test_patch_is_owner_scoped(client: AsyncClient):
    token_a = await _operator(client, "cfg-owner-a@example.com")
    token_b = await _operator(client, "cfg-owner-b@example.com")
    agent_a = await _seed_agent_for("cfg-owner-a@example.com")
    agent_b = await _seed_agent_for("cfg-owner-b@example.com")

    # B cannot read or modify A's agent, even by naming it explicitly.
    foreign_get = await client.get(f"/agent/config?agent_id={agent_a}", headers=_auth(token_b))
    assert foreign_get.status_code == 404
    foreign_patch = await client.patch(
        f"/agent/config?agent_id={agent_a}",
        json={"watchlist": ["TSLA"], "approval_mode": "AUTO"},
        headers=_auth(token_b),
    )
    assert foreign_patch.status_code == 404

    # B's own PATCH changes only B's agent.
    own_patch = await client.patch(
        "/agent/config",
        json={"watchlist": ["MSFT"], "approval_mode": "OBSERVE_ONLY"},
        headers=_auth(token_b),
    )
    assert own_patch.status_code == 200
    assert own_patch.json()["id"] == str(agent_b)

    a_view = (await client.get("/agent/config", headers=_auth(token_a))).json()
    assert a_view["id"] == str(agent_a)
    assert a_view["watchlist"] == ["AAPL", "NVDA", "SPY"]
    assert a_view["approval_mode"] == "REQUIRED"
    a_row = await _load_agent(agent_a)
    assert a_row.watchlist == ["AAPL", "NVDA", "SPY"]
    assert a_row.approval_mode == ApprovalMode.REQUIRED
    assert a_row.version == 1
    b_row = await _load_agent(agent_b)
    assert b_row.watchlist == ["MSFT"]
    assert b_row.approval_mode == ApprovalMode.OBSERVE_ONLY


@pytest.mark.asyncio
async def test_patch_requires_operator_role(client: AsyncClient):
    """A VIEWER/AUDITOR cannot approve trades, so they must not be able to flip the
    agent to AUTO (no human review) either. OPERATOR and ADMIN can."""
    email = "cfg-roles@example.com"
    token = await _register_and_login(client, email)  # VIEWER by default
    agent_id = await _seed_agent_for(email)

    for role in (UserRole.VIEWER, UserRole.AUDITOR):
        await _set_role(email, role)
        response = await client.patch(
            "/agent/config",
            json={"approval_mode": "AUTO", "watchlist": ["TSLA"]},
            headers=_auth(token),
        )
        assert response.status_code == 403, role
        agent = await _load_agent(agent_id)
        assert agent.approval_mode == ApprovalMode.REQUIRED
        assert agent.watchlist == ["AAPL", "NVDA", "SPY"]
    assert await _audit_events() == []

    await _set_role(email, UserRole.ADMIN)
    response = await client.patch(
        "/agent/config", json={"approval_mode": "OBSERVE_ONLY"}, headers=_auth(token)
    )
    assert response.status_code == 200
    assert response.json()["approval_mode"] == "OBSERVE_ONLY"


@pytest.mark.asyncio
async def test_patch_approval_mode(client: AsyncClient):
    """REQUIRED -> AUTO -> OBSERVE_ONLY -> REQUIRED persists, and the next cycle obeys it."""
    email = "cfg-mode@example.com"
    token = await _operator(client, email)
    agent_id = await _seed_agent_for(email, watchlist=["AAPL"])

    for mode in ("AUTO", "OBSERVE_ONLY", "REQUIRED"):
        response = await client.patch(
            "/agent/config", json={"approval_mode": mode}, headers=_auth(token)
        )
        assert response.status_code == 200
        assert response.json()["approval_mode"] == mode
        assert (await client.get("/agent/config", headers=_auth(token))).json()[
            "approval_mode"
        ] == mode
        assert (await _load_agent(agent_id)).approval_mode == ApprovalMode(mode)

    # Takes effect: in OBSERVE_ONLY a BUY never reaches the policy/execution gate.
    await client.patch(
        "/agent/config", json={"approval_mode": "OBSERVE_ONLY"}, headers=_auth(token)
    )
    gate = AsyncMock()
    with (
        patch("app.agent.orchestrator.get_llm_client", return_value=_llm("BUY", "AAPL")),
        patch(
            "app.agent.orchestrator.create_market_data_collector",
            return_value=_collector_for(["AAPL"]),
        ),
        patch("app.agent.orchestrator.evaluate_and_submit_cycle_trade", gate),
    ):
        response = await client.post("/agent/cycles/run", headers=_auth(token))

    assert response.status_code == 200
    cycle = response.json()
    assert cycle["agent_id"] == str(agent_id)
    assert cycle["proposal_json"]["action"] == "BUY"
    assert cycle["status"] == CycleStatus.COMPLETED.value
    assert cycle["snapshot_json"]["execution_status"] == "OBSERVE_ONLY"
    gate.assert_not_awaited()


@pytest.mark.asyncio
async def test_patch_records_audit_event(client: AsyncClient):
    email = "cfg-audit@example.com"
    token = await _operator(client, email)
    agent_id = await _seed_agent_for(email)
    async with TestSessionLocal() as db:
        user_id = (await db.execute(select(User.id).where(User.email == email))).scalar_one()

    response = await client.patch(
        "/agent/config",
        json={"watchlist": ["msft", "AAPL"], "approval_mode": "AUTO"},
        headers=_auth(token),
    )
    assert response.status_code == 200

    events = await _audit_events()
    assert len(events) == 1
    event = AuditEventRead.model_validate(events[0])  # the ORM row matches the schema
    assert event.event_type == "AGENT_CONFIG_UPDATED"
    assert event.entity_type == "agent_config"
    assert event.entity_id == agent_id
    assert event.actor_id == user_id
    assert event.actor_type == "USER"
    assert event.details == {
        "changes": {
            "watchlist": {"from": ["AAPL", "NVDA", "SPY"], "to": ["MSFT", "AAPL"]},
            "approval_mode": {"from": "REQUIRED", "to": "AUTO"},
        }
    }
    assert (await _load_agent(agent_id)).version == 2

    # A no-op PATCH (same values once normalized) changes nothing and writes no event.
    noop = await client.patch(
        "/agent/config",
        json={"watchlist": [" msft", "MSFT", "aapl"], "approval_mode": "AUTO"},
        headers=_auth(token),
    )
    assert noop.status_code == 200
    assert noop.json()["watchlist"] == ["MSFT", "AAPL"]
    assert len(await _audit_events()) == 1
    assert (await _load_agent(agent_id)).version == 2


@pytest.mark.asyncio
async def test_next_cycle_uses_updated_watchlist(client: AsyncClient):
    """After a PATCH the next cycle's context covers exactly the new symbols."""
    email = "cfg-next@example.com"
    token = await _operator(client, email)
    agent_id = await _seed_agent_for(email)  # AAPL / NVDA / SPY, no active cycles

    patched = await client.patch(
        "/agent/config", json={"watchlist": ["msft", " tsla "]}, headers=_auth(token)
    )
    assert patched.status_code == 200

    # Manual Trigger Now.
    collector = _collector_for(["MSFT", "TSLA"])
    with (
        patch("app.agent.orchestrator.get_llm_client", return_value=_llm("HOLD", "MSFT")),
        patch("app.agent.orchestrator.create_market_data_collector", return_value=collector),
    ):
        response = await client.post("/agent/cycles/run", headers=_auth(token))

    assert response.status_code == 200
    cycle = response.json()
    assert cycle["agent_id"] == str(agent_id)
    assert cycle["status"] == CycleStatus.HOLD.value
    assert cycle["snapshot_json"]["watchlist"] == ["MSFT", "TSLA"]
    assert set(cycle["snapshot_json"]["market"]) == {"MSFT", "TSLA"}
    assert collector.fetch_latest_bars.await_args.args[0] == ["MSFT", "TSLA"]

    # Scheduled path: the watchlist sweep runs one cycle per updated symbol.
    sweep_collector = _collector_for(["MSFT", "TSLA"])
    async with TestSessionLocal() as db:
        cycles = await run_watchlist_cycles(
            agent_id,
            db,
            trigger=CycleTrigger.SCHEDULED,
            llm_client=_llm("HOLD"),
            collector=sweep_collector,
        )
    assert [c.proposal_json["symbol"] for c in cycles] == ["MSFT", "TSLA"]
    assert all(c.snapshot_json["watchlist"] == ["MSFT", "TSLA"] for c in cycles)
    assert sweep_collector.fetch_latest_bars.await_args.args[0] == ["MSFT", "TSLA"]
