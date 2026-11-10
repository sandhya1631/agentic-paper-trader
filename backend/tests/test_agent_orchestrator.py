"""Unit and integration tests for Agent Context Builder & Orchestrator.

Stories 2.1.1, 2.1.2, 2.3.1, 2.3.2.
"""

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from conftest import TestSessionLocal
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.approval import reconcile_uncertain_cycle
from app.agent.context import build_agent_context
from app.agent.knowledge import retrieve_knowledge
from app.agent.orchestrator import (
    CycleOverlapError,
    _enforce_context_window,
    _select_dynamic_strategy,
    run_agent_cycle,
)
from app.agent.prompt import STRATEGY_REGISTRY, get_prompt_definition, get_system_prompt
from app.core.constants import DEFAULT_PROMPT_REVISION
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


def test_prompt_lists_only_buy_sell_hold():
    prompt = get_system_prompt("S-001")
    assert "STRATEGY RULES (" in prompt
    permitted_actions = prompt.partition("PERMITTED ACTIONS:")[2].partition(
        "STRATEGY RULES ("
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


def test_s002_is_short_json_focused_strategy():
    prompt = get_system_prompt("S-002")

    assert "STRATEGY VERSION: S-002" in prompt
    assert "account_data is LIVE" in prompt
    assert "Return only one valid JSON object" in prompt
    assert len(prompt) < len(get_system_prompt("S-001"))


def test_strategy_registry_contains_seven_evaluable_strategies():
    assert set(STRATEGY_REGISTRY) == {
        "S-001",
        "S-002",
        "S-003",
        "S-004",
        "S-005",
        "S-006",
        "S-007",
    }
    for version in STRATEGY_REGISTRY:
        prompt = get_system_prompt(version)
        assert f"STRATEGY VERSION: {version}" in prompt
        assert "BUY" in prompt
        assert "SELL" in prompt
        assert "HOLD" in prompt


def test_agent_config_rejects_unknown_strategy_version():
    agent = AgentConfig(strategy_version="S-001")

    with pytest.raises(ValueError, match="Unknown strategy version"):
        agent.strategy_version = "INVALID_VERSION"


def test_agent_config_rejects_unknown_strategy_version_on_creation():
    with pytest.raises(ValueError, match="Unknown strategy version"):
        AgentConfig(strategy_version="INVALID_VERSION")


def test_get_prompt_definition_records_revision_separately():
    definition = get_prompt_definition("S-001")

    assert definition.revision == DEFAULT_PROMPT_REVISION
    assert f"PROMPT REVISION: {DEFAULT_PROMPT_REVISION}" in definition.text


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
    assert len(context["market"]["AAPL"]["recent_bars"]) == 12
    assert context["market"]["AAPL"]["recent_bars"][-1]["close"] == 154.9
    assert context["market"]["AAPL"]["recent_bars"][-1]["change_pct"] > 0
    assert context["market"]["AAPL"]["data_quality"]["freshness_status"] == "STALE"
    assert context["market"]["NVDA"]["status"] == "NO_DATA"
    assert context["market"]["NVDA"]["data_quality"]["quality_status"] == "NO_DATA"
    # No account_data_status passed: the simpler convention infers UNAVAILABLE from
    # no account_info being given (#64/#172), not the richer NOT_PROVIDED placeholder.
    assert context["account"] == {"status": "UNAVAILABLE"}
    assert context["account_data"] == "UNAVAILABLE"


def test_build_agent_context_reports_market_data_quality():
    timestamp = datetime.now(timezone.utc).isoformat()
    bars = [
        {"timestamp": timestamp, "close": 100.0},
        {"timestamp": timestamp, "close": None},
        {"timestamp": timestamp, "close": 101.0},
    ]

    context = build_agent_context(
        ["AAPL"],
        {"AAPL": bars},
        max_data_age_seconds=60,
    )

    quality = context["market"]["AAPL"]["data_quality"]
    assert quality["freshness_status"] == "FRESH"
    assert quality["quality_status"] == "DEGRADED"
    assert quality["missing_close_count"] == 1
    assert quality["duplicate_timestamp_count"] == 2


def test_s001_requires_fresh_and_valid_market_data():
    prompt = get_system_prompt("S-001")

    assert "data_quality.freshness_status other than FRESH" in prompt
    assert "data_quality.quality_status other than VALID" in prompt


def test_build_agent_context_marks_unavailable_account_data():
    context = build_agent_context(
        ["AAPL"],
        {"AAPL": []},
        account_data_status="UNAVAILABLE_BROKER",
    )

    assert context["account"]["data_status"] == "UNAVAILABLE_BROKER"
    assert context["account"]["cash"] is None
    assert context["positions"] == []
    assert context["open_orders"] == []


def test_enforce_context_window_trims_payload_and_tracks_budget():
    context = {
        "watchlist": ["AAPL", "NVDA", "MSFT"],
        "market": {
            symbol: {
                "recent_bars": [{"close": float(index)} for index in range(20)],
                "extended_indicators": {"rsi_14": 60.0, "sma_20": 100.0, "noise": "x" * 200},
            }
            for symbol in ["AAPL", "NVDA", "MSFT"]
        },
        "account": {"data_status": "LIVE"},
        "positions": [{"symbol": "AAPL", "qty": 10}],
        "open_orders": [{"symbol": "AAPL", "qty": 1}],
        "decision_target": "AAPL",
    }

    trimmed = _enforce_context_window(
        context,
        "system " * 200,
        model_window_tokens=800,
    )

    assert trimmed["decision_target"] == "AAPL"
    assert trimmed["watchlist"] == ["AAPL"]
    assert trimmed["market"]["AAPL"]["recent_bars"][-1]["close"] == 19.0
    assert trimmed["context_window_guard"]["remaining_tokens"] >= 0
    assert trimmed["context_window_guard"]["model_window_tokens"] == 800


def test_dynamic_strategy_selection_explains_degraded_data_choice():
    selected, reason = _select_dynamic_strategy(
        {"market": {"AAPL": {"data_quality": {"quality_status": "DEGRADED"}}}},
        model_window_tokens=4096,
    )

    assert selected == "S-002"
    assert "degraded" in reason


def test_dynamic_strategy_selection_uses_regime_and_volume():
    selected, reason = _select_dynamic_strategy(
        {
            "market": {
                "AAPL": {
                    "regime": "UPTREND",
                    "relative_volume_20": 1.8,
                    "data_quality": {"quality_status": "VALID"},
                }
            }
        },
        model_window_tokens=4096,
    )

    assert selected == "S-004"
    assert "volume" in reason


def test_knowledge_retrieval_is_small_and_relevant():
    entries = retrieve_knowledge(
        strategy_version="S-004",
        market={
            "AAPL": {
                "regime": "UPTREND",
                "relative_volume_20": 1.8,
            }
        },
    )

    assert len(entries) <= 3
    assert {entry["topic"] for entry in entries} >= {"risk", "breakout"}


def test_build_agent_context_sanitizes_live_portfolio_data():
    context = build_agent_context(
        ["AAPL"],
        {"AAPL": []},
        account_info={
            "cash": "12000.00",
            "equity": "25000.00",
            "buying_power": "48000.00",
            "daily_pnl": "-125.00",
        },
        positions=[
            {
                "symbol": "AAPL",
                "qty": "10",
                "market_value": "1800.00",
                "unrealized_pl": "-25.00",
            }
        ],
        open_orders=[
            {
                "id": "order-1",
                "symbol": "AAPL",
                "side": "buy",
                "qty": "2",
                "status": "new",
            }
        ],
        account_data_status="LIVE",
    )

    assert context["account"] == {
        "cash": "12000.00",
        "equity": "25000.00",
        "buying_power": "48000.00",
        "daily_pnl": "-125.00",
        "data_status": "LIVE",
    }
    assert context["positions"] == [
        {
            "symbol": "AAPL",
            "qty": "10",
            "market_value": "1800.00",
            "unrealized_pnl": "-25.00",
        }
    ]
    assert context["open_orders"] == [
        {
            "id": "order-1",
            "symbol": "AAPL",
            "side": "buy",
            "qty": "2",
            "status": "new",
        }
    ]
    assert "encrypted_api_key" not in json.dumps(context)


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
    assert cycle.snapshot_json["observability"]["symbols"] == 2
    assert cycle.snapshot_json["observability"]["bars_used"] == 50
    assert cycle.snapshot_json["observability"]["context_chars"] > 0
    assert cycle.snapshot_json["observability"]["estimated_input_tokens"] > 0
    assert cycle.prompt_version == agent.strategy_version
    assert cycle.prompt_revision == DEFAULT_PROMPT_REVISION
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
    no_expired_approvals_result = MagicMock()
    no_expired_approvals_result.all.return_value = []

    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[agent_result, no_expired_approvals_result, no_active_cycle_result]
    )
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
    no_expired_approvals_result = MagicMock()
    no_expired_approvals_result.all.return_value = []
    added = []

    def refresh(model):
        if isinstance(model, AgentCycle) and model.id is None:
            model.id = uuid.uuid4()

    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[agent_result, no_expired_approvals_result, no_active_cycle_result]
    )
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
    no_expired_approvals_result = MagicMock()
    no_expired_approvals_result.all.return_value = []
    added = []

    def refresh(model):
        if isinstance(model, AgentCycle) and model.id is None:
            model.id = uuid.uuid4()

    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[agent_result, no_expired_approvals_result, no_active_cycle_result]
    )
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
        decision="ALLOW",
        model_dump=lambda **kwargs: {"decision": "ALLOW"},
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
    assert cycle.approval is None


@pytest.mark.asyncio
async def test_reconciliation_clears_cycle_when_submission_never_started():
    cycle = SimpleNamespace(
        id=uuid.uuid4(),
        status=CycleStatus.RECONCILING,
        failure_code="ORDER_EXECUTION_UNCERTAIN",
        failure_message="temporary failure",
        completed_at=None,
        snapshot_json={"execution_status": "RECONCILIATION_REQUIRED"},
    )
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: None)
        ),
        commit=AsyncMock(),
    )

    resolved = await reconcile_uncertain_cycle(db, SimpleNamespace(), cycle)

    assert resolved is True
    assert cycle.status == CycleStatus.FAILED
    assert cycle.failure_code == "ORDER_SUBMISSION_NOT_STARTED"
    assert cycle.completed_at is not None
    db.commit.assert_awaited_once()


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
async def test_manual_trigger_expires_stale_approval_instead_of_blocking(
    db_session: AsyncSession,
):
    """A forgotten PENDING_APPROVAL cycle past its expiry must not block forever (#106)."""
    agent = await _make_agent(db_session, "expired-approval@example.com")

    stuck_cycle = AgentCycle(
        agent_id=agent.id,
        trigger=CycleTrigger.MANUAL,
        status=CycleStatus.PENDING_APPROVAL,
    )
    db_session.add(stuck_cycle)
    await db_session.flush()

    stale_approval = CycleApproval(
        cycle_id=stuck_cycle.id,
        status=ApprovalStatus.PENDING,
        proposal_hash="x" * 64,
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db_session.add(stale_approval)
    await db_session.commit()

    mock_llm = MagicMock()
    mock_llm.generate = AsyncMock(
        return_value=json.dumps({"symbol": "AAPL", "action": "HOLD", "reasoning": "no signal"})
    )
    mock_collector = MagicMock()
    mock_collector.fetch_latest_bars = AsyncMock(return_value={"AAPL": []})

    cycle = await run_agent_cycle(
        agent.id, db_session, llm_client=mock_llm, collector=mock_collector
    )

    assert cycle.id != stuck_cycle.id
    assert cycle.status == CycleStatus.HOLD

    await db_session.refresh(stuck_cycle)
    await db_session.refresh(stale_approval)
    assert stuck_cycle.status == CycleStatus.REJECTED
    assert stuck_cycle.failure_code == "APPROVAL_EXPIRED"
    assert stale_approval.status == ApprovalStatus.EXPIRED


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

    async with TestSessionLocal() as db:
        user_result = await db.execute(select(User).where(User.email == email))
        user = user_result.scalar_one()
        user.role = UserRole.OPERATOR  # running cycles is operator-only (#176)
        account = TradingAccount(
            user_id=user.id,
            provider="ALPACA",
            encrypted_api_key="enc_key",
            encrypted_api_secret="enc_secret",
            is_paper=True,
        )
        db.add(account)
        await db.flush()
        policy_set = PolicySet(
            version=f"P-{uuid.uuid4()}",
            limits_json={},
            is_active=True,
        )
        db.add(policy_set)
        await db.flush()
        agent = AgentConfig(
            account_id=account.id,
            policy_set_id=policy_set.id,
            name="HTTP Overlap Agent",
            watchlist=["AAPL"],
            strategy_version="S-001",
            approval_mode=ApprovalMode.REQUIRED,
            enabled=True,
            model_provider="ollama",
            model_name="llama3.1",
        )
        db.add(agent)
        await db.commit()
        some_agent_id = agent.id

    with patch(
        "app.agent.router.run_agent_cycle",
        side_effect=CycleOverlapError(f"Agent '{some_agent_id}' already has an active cycle"),
    ):
        response = await client.post(
            f"/agent/cycles/run?agent_id={some_agent_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 409
