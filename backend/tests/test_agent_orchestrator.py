"""Unit and integration tests for Agent Context Builder & Orchestrator.

Stories 2.1.1, 2.1.2, 2.3.1, 2.3.2.
"""

import json
import re
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import build_agent_context
from app.agent.orchestrator import CycleOverlapError, run_agent_cycle
from app.agent.prompt import get_prompt_definition, get_system_prompt
from app.db.enums import ApprovalMode, CycleStatus, CycleTrigger, UserRole
from app.db.models import AgentConfig, AgentCycle, PolicySet, TradingAccount, User
from app.market_data.alpaca_client import OHLCVBar


def _make_bar(price: float) -> OHLCVBar:
    return OHLCVBar(
        timestamp=datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc).isoformat(),
        open=price,
        high=price + 1.0,
        low=price - 1.0,
        close=price,
        volume=1000.0,
    )


# --- Context & Prompt Unit Tests ---


def test_get_system_prompt_s001():
    prompt = get_system_prompt("S-001")
    assert "STRATEGY VERSION: S-001" in prompt
    assert "BUY" in prompt
    assert "SELL" in prompt
    assert "HOLD" in prompt


def test_prompt_lists_only_buy_sell_hold():
    prompt = get_system_prompt("S-001")
    permitted_actions = prompt.partition("PERMITTED ACTIONS:")[2].partition(
        "STRATEGY RULES:"
    )[0]

    assert permitted_actions
    actions = re.findall(r"^\d+\.\s+([A-Z]+)", permitted_actions, re.MULTILINE)
    assert actions == ["BUY", "SELL", "HOLD"]
    assert "No other action is permitted." in permitted_actions
    assert "SHORT" not in prompt
    assert "MARGIN" not in prompt


def test_prompt_requires_numeric_evidence():
    prompt = get_system_prompt("S-001")

    assert "exact numerical values" in prompt
    assert "exact RSI-14 value" in prompt
    assert "exact SMA-20 and SMA-50 values" in prompt
    assert "current price and cash balances" in prompt


def test_prompt_instructs_hold_on_uncertainty():
    prompt = get_system_prompt("S-001")

    assert "IF uncertain -> DEFAULT TO HOLD." in prompt
    assert "NEVER GUESS OR FABRICATE DATA." in prompt
    assert "MUST output HOLD" in prompt


def test_get_system_prompt_unknown_raises():
    with pytest.raises(ValueError):
        get_system_prompt("INVALID_VERSION")


def test_get_prompt_definition_records_revision_separately():
    definition = get_prompt_definition("S-001")

    assert definition.revision == "1.0.0"
    assert "PROMPT REVISION: 1.0.0" in definition.text


def test_build_agent_context():
    symbols = ["AAPL", "NVDA"]
    # 50 bars to trigger indicator calculation
    bars_aapl = [_make_bar(150.0 + i * 0.1) for i in range(50)]
    bars_dict = {"AAPL": bars_aapl, "NVDA": []}

    context = build_agent_context(symbols, bars_dict, strategy_version="S-001")

    assert context["strategy_version"] == "S-001"
    assert context["watchlist"] == ["AAPL", "NVDA"]
    assert context["market"]["AAPL"]["latest_price"] == 154.9
    assert context["market"]["AAPL"]["rsi_14"] is not None
    assert context["market"]["NVDA"]["status"] == "NO_DATA"


# --- Orchestrator Integration Tests ---


@pytest.mark.asyncio
async def test_run_agent_cycle_success(db_session: AsyncSession):
    # Setup test user, account, policy set, and agent config
    user = User(email="agent-test@example.com", password_hash="hash", role=UserRole.ADMIN)
    db_session.add(user)
    await db_session.flush()

    account = TradingAccount(
        user_id=user.id,
        provider="ALPACA",
        encrypted_api_key="enc_key",
        encrypted_api_secret="enc_secret",
        is_paper=True,
        is_connected=True,
    )
    db_session.add(account)
    await db_session.flush()

    policy_set = PolicySet(version="P-v1", limits_json={}, is_active=True)
    db_session.add(policy_set)
    await db_session.flush()

    agent = AgentConfig(
        account_id=account.id,
        policy_set_id=policy_set.id,
        name="Test Agent",
        watchlist=["AAPL", "NVDA"],
        strategy_version="S-001",
        approval_mode=ApprovalMode.REQUIRED,
        enabled=True,
        model_provider="ollama",
        model_name="llama3.1",
    )
    db_session.add(agent)
    await db_session.commit()
    await db_session.refresh(agent)

    # Mock LLM Client returning valid DecisionProposal JSON
    mock_llm = MagicMock()
    valid_json = json.dumps(
        {
            "symbol": "AAPL",
            "action": "BUY",
            "quantity": 10,
            "reasoning": (
                "RSI-14 is 32.5 (oversold) and price is above SMA-20. "
                "Citing RSI=32.5, SMA20=149.50."
            ),
        }
    )
    mock_llm.generate = AsyncMock(return_value=valid_json)

    # Mock Market Collector
    mock_collector = MagicMock()
    fake_bars = {"AAPL": [_make_bar(150.0) for _ in range(50)], "NVDA": []}
    mock_collector.fetch_latest_bars = AsyncMock(return_value=fake_bars)

    cycle = await run_agent_cycle(
        agent.id,
        db_session,
        trigger=CycleTrigger.MANUAL,
        llm_client=mock_llm,
        collector=mock_collector,
    )

    assert cycle.status == CycleStatus.COMPLETED
    assert cycle.proposal_json["action"] == "BUY"
    assert cycle.proposal_json["symbol"] == "AAPL"
    assert cycle.snapshot_json["watchlist"] == ["AAPL", "NVDA"]
    assert cycle.prompt_version == agent.strategy_version
    assert cycle.prompt_revision == "1.0.0"
    assert "S-001" in cycle.prompt_text


@pytest.mark.asyncio
async def test_run_agent_cycle_self_repair_on_invalid_json(db_session: AsyncSession):
    user = User(email="repair-test@example.com", password_hash="hash", role=UserRole.ADMIN)
    db_session.add(user)
    await db_session.flush()

    account = TradingAccount(
        user_id=user.id,
        provider="ALPACA",
        encrypted_api_key="enc_key",
        encrypted_api_secret="enc_secret",
        is_paper=True,
    )
    db_session.add(account)
    await db_session.flush()

    policy_set = PolicySet(version="P-v2", limits_json={}, is_active=True)
    db_session.add(policy_set)
    await db_session.flush()

    agent = AgentConfig(
        account_id=account.id,
        policy_set_id=policy_set.id,
        name="Repair Agent",
        watchlist=["SPY"],
        strategy_version="S-001",
        approval_mode=ApprovalMode.REQUIRED,
        enabled=True,
        model_provider="ollama",
        model_name="llama3.1",
    )
    db_session.add(agent)
    await db_session.commit()
    await db_session.refresh(agent)

    # Mock LLM Client: 1st response is malformed JSON, 2nd (repaired) response is valid
    invalid_json = "I think we should buy 5 shares of SPY because RSI is low."
    valid_json = json.dumps(
        {
            "symbol": "SPY",
            "action": "BUY",
            "quantity": 5,
            "reasoning": "Repaired JSON: RSI is 35.0, price above SMA-20.",
        }
    )
    mock_llm = MagicMock()
    mock_llm.generate = AsyncMock(side_effect=[invalid_json, valid_json])

    mock_collector = MagicMock()
    mock_collector.fetch_latest_bars = AsyncMock(return_value={"SPY": []})

    cycle = await run_agent_cycle(
        agent.id,
        db_session,
        trigger=CycleTrigger.MANUAL,
        llm_client=mock_llm,
        collector=mock_collector,
    )

    assert mock_llm.generate.call_count == 2
    assert cycle.status == CycleStatus.COMPLETED
    assert cycle.proposal_json["action"] == "BUY"
    assert cycle.proposal_json["symbol"] == "SPY"


async def _make_agent(db_session: AsyncSession, email: str) -> AgentConfig:
    user = User(email=email, password_hash="hash", role=UserRole.ADMIN)
    db_session.add(user)
    await db_session.flush()

    account = TradingAccount(
        user_id=user.id,
        provider="ALPACA",
        encrypted_api_key="enc_key",
        encrypted_api_secret="enc_secret",
        is_paper=True,
    )
    db_session.add(account)
    await db_session.flush()

    policy_set = PolicySet(version=f"P-{uuid.uuid4()}", limits_json={}, is_active=True)
    db_session.add(policy_set)
    await db_session.flush()

    agent = AgentConfig(
        account_id=account.id,
        policy_set_id=policy_set.id,
        name="Overlap Test Agent",
        watchlist=["AAPL"],
        strategy_version="S-001",
        approval_mode=ApprovalMode.REQUIRED,
        enabled=True,
        model_provider="ollama",
        model_name="llama3.1",
    )
    db_session.add(agent)
    await db_session.commit()
    await db_session.refresh(agent)
    return agent


# --- Cycle overlap (Story 2.3.2 / Issue #68: active cycles cannot overlap) ---


@pytest.mark.asyncio
async def test_run_agent_cycle_rejects_when_active_cycle_exists(db_session: AsyncSession):
    agent = await _make_agent(db_session, "overlap-pending@example.com")

    existing = AgentCycle(
        agent_id=agent.id,
        trigger=CycleTrigger.MANUAL,
        status=CycleStatus.PENDING,
    )
    db_session.add(existing)
    await db_session.commit()

    mock_llm = MagicMock()
    mock_llm.generate = AsyncMock(return_value="{}")

    with pytest.raises(CycleOverlapError, match=str(existing.id)):
        await run_agent_cycle(agent.id, db_session, llm_client=mock_llm)

    # No new cycle should be created, and the LLM should never be called.
    mock_llm.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_agent_cycle_allowed_after_previous_cycle_completed(
    db_session: AsyncSession,
):
    agent = await _make_agent(db_session, "overlap-completed@example.com")

    finished = AgentCycle(
        agent_id=agent.id,
        trigger=CycleTrigger.MANUAL,
        status=CycleStatus.COMPLETED,
    )
    db_session.add(finished)
    await db_session.commit()

    mock_llm = MagicMock()
    mock_llm.generate = AsyncMock(
        return_value=json.dumps(
            {"symbol": "AAPL", "action": "HOLD", "reasoning": "no signal"}
        )
    )
    mock_collector = MagicMock()
    mock_collector.fetch_latest_bars = AsyncMock(return_value={"AAPL": []})

    cycle = await run_agent_cycle(
        agent.id, db_session, llm_client=mock_llm, collector=mock_collector
    )

    assert cycle.status == CycleStatus.COMPLETED
    assert cycle.id != finished.id


@pytest.mark.asyncio
async def test_trigger_cycle_endpoint_returns_409_on_overlap(client: AsyncClient) -> None:
    email = f"overlap-http-{uuid.uuid4()}@example.com"
    password = "S3cure-Password!"
    await client.post("/auth/register", json={"email": email, "password": password})
    login = await client.post("/auth/login", json={"email": email, "password": password})
    token = login.json()["access_token"]

    some_agent_id = uuid.uuid4()
    with patch(
        "app.agent.router.run_agent_cycle",
        side_effect=CycleOverlapError(f"Agent '{some_agent_id}' already has an active cycle"),
    ):
        response = await client.post(
            f"/agent/cycles/run?agent_id={some_agent_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 409
