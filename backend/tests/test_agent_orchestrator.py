"""Unit and integration tests for Agent Context Builder & Orchestrator.

Stories 2.1.1, 2.1.2, 2.3.1, 2.3.2.
"""

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import build_agent_context
from app.agent.orchestrator import CycleOverlapError, run_agent_cycle
from app.agent.prompt import get_system_prompt
from app.db.enums import ApprovalMode, ApprovalStatus, CycleStatus, CycleTrigger, UserRole
from app.db.models import (
    AgentConfig,
    AgentCycle,
    CycleApproval,
    PolicySet,
    TradingAccount,
    User,
)
from app.market_data.alpaca_client import OHLCVBar
from app.risk.policies import POLICY_VERSION


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


def test_agent_config_rejects_unknown_strategy_version():
    agent = AgentConfig(strategy_version="S-001")

    with pytest.raises(ValueError, match="Unknown strategy version"):
        agent.strategy_version = "INVALID_VERSION"


def test_agent_config_rejects_unknown_strategy_version_on_creation():
    with pytest.raises(ValueError, match="Unknown strategy version"):
        AgentConfig(strategy_version="INVALID_VERSION")


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

    assert cycle.status == CycleStatus.PENDING_APPROVAL
    assert cycle.proposal_json["action"] == "BUY"
    assert cycle.proposal_json["symbol"] == "AAPL"
    assert cycle.snapshot_json["watchlist"] == ["AAPL", "NVDA"]
    assert cycle.snapshot_json["strategy_version"] == "S-001"
    assert cycle.snapshot_json["versions"] == {
        "strategy": "S-001",
        "policy": POLICY_VERSION,
    }
    assert cycle.snapshot_json["execution_status"] == "PENDING_APPROVAL"
    assert "S-001" in cycle.prompt_text
    assert mock_llm.generate.await_args.kwargs["system"] == get_system_prompt("S-001")
    assert cycle.approval.status == ApprovalStatus.PENDING


@pytest.mark.asyncio
async def test_run_agent_cycle_records_version_metadata_without_database():
    agent = SimpleNamespace(
        id=uuid.uuid4(),
        watchlist=["AAPL"],
        strategy_version="S-001",
    )
    agent_result = MagicMock()
    agent_result.scalar_one_or_none.return_value = agent
    no_active_cycle_result = MagicMock()
    no_active_cycle_result.scalars.return_value.first.return_value = None

    db = MagicMock()
    db.execute = AsyncMock(side_effect=[agent_result, no_active_cycle_result])
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    mock_llm = MagicMock()
    mock_llm.generate = AsyncMock(
        return_value=json.dumps(
            {
                "symbol": "AAPL",
                "action": "HOLD",
                "quantity": 0,
                "reasoning": "No signal.",
            }
        )
    )
    mock_collector = MagicMock()
    mock_collector.fetch_latest_bars = AsyncMock(return_value={"AAPL": []})

    cycle = await run_agent_cycle(
        agent.id,
        db,
        llm_client=mock_llm,
        collector=mock_collector,
    )

    assert cycle.snapshot_json["strategy_version"] == "S-001"
    assert cycle.snapshot_json["versions"] == {
        "strategy": "S-001",
        "policy": POLICY_VERSION,
    }
    assert mock_llm.generate.await_args.kwargs["system"] == get_system_prompt("S-001")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "trigger",
    [CycleTrigger.MANUAL, CycleTrigger.SCHEDULED],
)
async def test_required_buy_creates_pending_approval_without_database(trigger):
    agent = SimpleNamespace(
        id=uuid.uuid4(),
        watchlist=["AAPL"],
        strategy_version="S-001",
        approval_mode=ApprovalMode.REQUIRED,
    )
    agent_result = MagicMock()
    agent_result.scalar_one_or_none.return_value = agent
    no_active_cycle_result = MagicMock()
    no_active_cycle_result.scalars.return_value.first.return_value = None
    added = []

    def refresh(model):
        if isinstance(model, AgentCycle) and model.id is None:
            model.id = uuid.uuid4()

    db = MagicMock()
    db.execute = AsyncMock(side_effect=[agent_result, no_active_cycle_result])
    db.add = MagicMock(side_effect=added.append)
    db.commit = AsyncMock()
    db.refresh = AsyncMock(side_effect=refresh)

    mock_llm = MagicMock()
    mock_llm.generate = AsyncMock(
        return_value=json.dumps(
            {
                "symbol": "AAPL",
                "action": "BUY",
                "quantity": 1,
                "reasoning": "Test proposal",
            }
        )
    )
    collector = MagicMock()
    collector.fetch_latest_bars = AsyncMock(return_value={"AAPL": []})

    cycle = await run_agent_cycle(
        agent.id,
        db,
        trigger=trigger,
        llm_client=mock_llm,
        collector=collector,
    )

    approval = next(item for item in added if isinstance(item, CycleApproval))
    assert cycle.status == CycleStatus.PENDING_APPROVAL
    assert approval.status == ApprovalStatus.PENDING
    assert approval.cycle_id == cycle.id
    assert len(approval.proposal_hash) == 64
    assert cycle.completed_at is None


@pytest.mark.asyncio
async def test_scheduled_auto_buy_executes_without_human_approval(monkeypatch):
    agent = SimpleNamespace(
        id=uuid.uuid4(),
        watchlist=["AAPL"],
        strategy_version="S-001",
        approval_mode=ApprovalMode.AUTO,
    )
    agent_result = MagicMock()
    agent_result.scalar_one_or_none.return_value = agent
    no_active_cycle_result = MagicMock()
    no_active_cycle_result.scalars.return_value.first.return_value = None
    added = []

    def refresh(model):
        if isinstance(model, AgentCycle) and model.id is None:
            model.id = uuid.uuid4()

    db = MagicMock()
    db.execute = AsyncMock(side_effect=[agent_result, no_active_cycle_result])
    db.add = MagicMock(side_effect=added.append)
    db.commit = AsyncMock()
    db.refresh = AsyncMock(side_effect=refresh)

    mock_llm = MagicMock()
    mock_llm.generate = AsyncMock(
        return_value=json.dumps(
            {
                "symbol": "AAPL",
                "action": "BUY",
                "quantity": 1,
                "reasoning": "Scheduled proposal",
            }
        )
    )
    collector = MagicMock()
    collector.fetch_latest_bars = AsyncMock(return_value={"AAPL": []})
    policy_decision = SimpleNamespace(
        model_dump=lambda mode: {"decision": "ALLOW"}
    )
    broker_order = SimpleNamespace(status=SimpleNamespace(value="SUBMITTED"))
    execute_trade = AsyncMock(return_value=(policy_decision, broker_order))
    monkeypatch.setattr(
        "app.agent.orchestrator.evaluate_and_submit_cycle_trade",
        execute_trade,
    )

    cycle = await run_agent_cycle(
        agent.id,
        db,
        trigger=CycleTrigger.SCHEDULED,
        llm_client=mock_llm,
        collector=collector,
    )

    execute_trade.assert_awaited_once_with(db, agent, cycle)
    assert not any(isinstance(item, CycleApproval) for item in added)
    assert cycle.status == CycleStatus.COMPLETED
    assert cycle.snapshot_json["execution_status"] == "ORDER_SUBMITTED"


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
    assert cycle.status == CycleStatus.PENDING_APPROVAL
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

    assert cycle.status == CycleStatus.HOLD
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
