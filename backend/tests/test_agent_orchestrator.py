"""Unit and integration tests for Agent Context Builder & Orchestrator.

Stories 2.1.1, 2.1.2, 2.3.1, 2.3.2.
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import build_agent_context
from app.agent.orchestrator import run_agent_cycle
from app.agent.prompt import get_system_prompt
from app.db.enums import ApprovalMode, CycleStatus, CycleTrigger, UserRole
from app.db.models import AgentConfig, PolicySet, TradingAccount, User
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


def test_get_system_prompt_unknown_raises():
    with pytest.raises(ValueError):
        get_system_prompt("INVALID_VERSION")


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
