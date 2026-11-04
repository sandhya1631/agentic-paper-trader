"""Multi-symbol watchlist evaluation (#153).

The scheduled tick runs one decision cycle per watchlist symbol (a "sweep") via
`run_watchlist_cycles`, instead of a single (historically AAPL-anchored) decision.
These tests lock in: one cycle per symbol, per-symbol scoping, the scope guard
that never acts on an un-targeted symbol, the overlap bypass (one symbol's pending
approval must not block the next), per-symbol guardrails, and a de-anchored prompt.
"""

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.orchestrator import run_watchlist_cycles
from app.agent.prompt import get_system_prompt
from app.db.enums import (
    ApprovalMode,
    CycleStatus,
    CycleTrigger,
    PolicyDecisionType,
    UserRole,
)
from app.db.models import (
    AgentConfig,
    AgentCycle,
    PolicySet,
    TradingAccount,
    User,
)
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


async def _seed_agent(
    db: AsyncSession,
    *,
    watchlist: list[str],
    approval_mode: ApprovalMode,
) -> AgentConfig:
    user = User(
        email=f"ms-{uuid.uuid4()}@example.com", password_hash="h", role=UserRole.ADMIN
    )
    db.add(user)
    await db.flush()
    account = TradingAccount(
        user_id=user.id,
        provider="ALPACA",
        encrypted_api_key="k",
        encrypted_api_secret="s",
        is_paper=True,
        is_connected=True,
    )
    db.add(account)
    await db.flush()
    policy_set = PolicySet(version="P-v1", limits_json={}, is_active=True)
    db.add(policy_set)
    await db.flush()
    agent = AgentConfig(
        account_id=account.id,
        policy_set_id=policy_set.id,
        name="Multi-symbol Agent",
        watchlist=watchlist,
        strategy_version="S-001",
        approval_mode=approval_mode,
        enabled=True,
        model_provider="ollama",
        model_name="llama3.1",
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return agent


def _echo_target_llm() -> MagicMock:
    """A compliant model: it decides BUY for whatever `decision_target` the cycle sets."""

    def gen(prompt_input: str, system: str | None = None) -> str:
        ctx = json.loads(prompt_input)
        sym = ctx.get("decision_target", "AAPL")
        return json.dumps(
            {
                "symbol": sym,
                "action": "BUY",
                "quantity": 1,
                "reasoning": "RSI-14 is 30.0 (oversold). Citing RSI=30.0, SMA20=1.00.",
            }
        )

    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=gen)
    return llm


def _fixed_symbol_llm(symbol: str = "AAPL") -> MagicMock:
    """A mis-behaving model: it always answers for one symbol, ignoring the target."""

    def gen(prompt_input: str, system: str | None = None) -> str:
        return json.dumps(
            {
                "symbol": symbol,
                "action": "BUY",
                "quantity": 1,
                "reasoning": "RSI-14 is 30.0 (oversold). Citing RSI=30.0, SMA20=1.00.",
            }
        )

    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=gen)
    return llm


def _collector_for(watchlist: list[str]) -> MagicMock:
    collector = MagicMock()
    bars = {s: [_make_bar(150.0) for _ in range(50)] for s in watchlist}
    collector.fetch_latest_bars = AsyncMock(return_value=bars)
    return collector


@pytest.mark.asyncio
async def test_sweep_creates_one_cycle_per_symbol(db_session: AsyncSession):
    # Includes symbols beyond the legacy AAPL/NVDA/SPY enum, proving the widening.
    watchlist = ["AAPL", "NVDA", "MSFT", "TSLA"]
    agent = await _seed_agent(db_session, watchlist=watchlist, approval_mode=ApprovalMode.REQUIRED)

    cycles = await run_watchlist_cycles(
        agent.id,
        db_session,
        trigger=CycleTrigger.SCHEDULED,
        llm_client=_echo_target_llm(),
        collector=_collector_for(watchlist),
    )

    assert len(cycles) == len(watchlist)
    # Exactly one cycle per watchlist symbol, in order.
    assert [c.proposal_json["symbol"] for c in cycles] == watchlist
    # Every BUY parked for approval — proving the pipeline ran per symbol AND that
    # the first symbol's PENDING_APPROVAL did not block the rest (overlap bypass).
    assert all(c.status == CycleStatus.PENDING_APPROVAL for c in cycles)
    assert all(c.trigger == CycleTrigger.SCHEDULED for c in cycles)

    # Persisted audit trail: exactly one distinct AgentCycle row per symbol.
    rows = (
        await db_session.execute(select(AgentCycle).where(AgentCycle.agent_id == agent.id))
    ).scalars().all()
    assert len(rows) == len(watchlist)
    assert len({r.id for r in rows}) == len(watchlist)
    assert {r.proposal_json["symbol"] for r in rows} == set(watchlist)


@pytest.mark.asyncio
async def test_sweep_decisions_are_not_anchored_to_first_symbol(db_session: AsyncSession):
    watchlist = ["AAPL", "NVDA", "MSFT"]
    agent = await _seed_agent(db_session, watchlist=watchlist, approval_mode=ApprovalMode.REQUIRED)

    cycles = await run_watchlist_cycles(
        agent.id,
        db_session,
        llm_client=_echo_target_llm(),
        collector=_collector_for(watchlist),
    )

    acted_symbols = {
        c.proposal_json["symbol"] for c in cycles if c.proposal_json["action"] == "BUY"
    }
    # Variety: the agent acts across the whole watchlist, not only the first symbol.
    assert acted_symbols == set(watchlist)


@pytest.mark.asyncio
async def test_sweep_fetches_bars_once(db_session: AsyncSession):
    watchlist = ["AAPL", "NVDA", "MSFT"]
    agent = await _seed_agent(db_session, watchlist=watchlist, approval_mode=ApprovalMode.REQUIRED)
    collector = _collector_for(watchlist)

    await run_watchlist_cycles(
        agent.id, db_session, llm_client=_echo_target_llm(), collector=collector
    )

    # Bars are fetched once for the whole watchlist and reused across per-symbol cycles.
    collector.fetch_latest_bars.assert_awaited_once()


@pytest.mark.asyncio
async def test_sweep_coerces_off_target_proposal_to_hold(db_session: AsyncSession):
    """Scope guard: a model that answers for the wrong symbol must never cause an
    action on a symbol this cycle did not evaluate — it is recorded as a HOLD."""
    watchlist = ["AAPL", "NVDA", "MSFT"]
    agent = await _seed_agent(db_session, watchlist=watchlist, approval_mode=ApprovalMode.REQUIRED)

    cycles = await run_watchlist_cycles(
        agent.id,
        db_session,
        llm_client=_fixed_symbol_llm("AAPL"),  # always answers AAPL
        collector=_collector_for(watchlist),
    )

    by_symbol = {c.proposal_json["symbol"]: c for c in cycles}
    # AAPL got a real BUY; the other symbols were coerced to HOLD (not acted on).
    assert by_symbol["AAPL"].proposal_json["action"] == "BUY"
    assert by_symbol["AAPL"].status == CycleStatus.PENDING_APPROVAL
    for sym in ("NVDA", "MSFT"):
        assert by_symbol[sym].proposal_json["action"] == "HOLD"
        assert by_symbol[sym].status == CycleStatus.HOLD


@pytest.mark.asyncio
async def test_sweep_runs_guardrails_per_symbol(db_session: AsyncSession):
    """Each per-symbol AUTO cycle runs the execution/policy gate, and a policy
    denial rejects that symbol's cycle. The gate is invoked once per symbol."""
    watchlist = ["AAPL", "NVDA", "MSFT"]
    agent = await _seed_agent(db_session, watchlist=watchlist, approval_mode=ApprovalMode.AUTO)

    deny = MagicMock()
    deny.decision = PolicyDecisionType.DENY
    deny.model_dump = MagicMock(
        return_value={
            "decision": "DENY",
            "evaluations": [
                {"policy_id": "P-004", "outcome": "FAIL", "reason": "Market data is stale"}
            ],
        }
    )
    gate = AsyncMock(return_value=(deny, None))  # (policy_decision, broker_order=None → denied)

    with patch("app.agent.orchestrator.evaluate_and_submit_cycle_trade", gate):
        cycles = await run_watchlist_cycles(
            agent.id,
            db_session,
            llm_client=_echo_target_llm(),
            collector=_collector_for(watchlist),
        )

    assert len(cycles) == len(watchlist)
    # The execution/policy gate ran once per symbol (guardrails are per-symbol)...
    assert gate.await_count == len(watchlist)
    # ...and a policy denial rejected each symbol's cycle.
    assert all(c.status == CycleStatus.REJECTED for c in cycles)
    assert all(c.failure_code == "POLICY_DENIED" for c in cycles)
    assert all(c.policy_decision == PolicyDecisionType.DENY for c in cycles)


@pytest.mark.asyncio
async def test_sweep_empty_watchlist_returns_no_cycles(db_session: AsyncSession):
    agent = await _seed_agent(db_session, watchlist=[], approval_mode=ApprovalMode.REQUIRED)

    cycles = await run_watchlist_cycles(
        agent.id, db_session, llm_client=_echo_target_llm(), collector=_collector_for([])
    )

    assert cycles == []


@pytest.mark.asyncio
async def test_sweep_continues_after_one_symbol_errors(db_session: AsyncSession):
    """A transient failure on one symbol must not drop the rest of the watchlist."""
    watchlist = ["AAPL", "NVDA", "MSFT"]
    agent = await _seed_agent(db_session, watchlist=watchlist, approval_mode=ApprovalMode.REQUIRED)

    def gen(prompt_input: str, system: str | None = None) -> str:
        sym = json.loads(prompt_input).get("decision_target", "AAPL")
        if sym == "NVDA":
            raise RuntimeError("transient LLM failure")
        return json.dumps(
            {"symbol": sym, "action": "HOLD", "quantity": 0, "reasoning": "Citing RSI=50, SMA20=1."}
        )

    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=gen)

    cycles = await run_watchlist_cycles(
        agent.id, db_session, llm_client=llm, collector=_collector_for(watchlist)
    )

    # NVDA failed and is excluded from the returned list, but AAPL and MSFT still ran.
    assert [c.proposal_json["symbol"] for c in cycles] == ["AAPL", "MSFT"]
    # The failed symbol still left an audited FAILED cycle row.
    rows = (
        await db_session.execute(select(AgentCycle).where(AgentCycle.agent_id == agent.id))
    ).scalars().all()
    assert len(rows) == len(watchlist)
    nvda = next(r for r in rows if r.status == CycleStatus.FAILED)
    assert nvda.failure_code == "UNEXPECTED_ERROR"


@pytest.mark.asyncio
async def test_sweep_raises_when_every_symbol_fails(db_session: AsyncSession):
    """If no symbol succeeds (e.g. the LLM is down), the sweep surfaces the error so
    the scheduler reports the tick as FAILED instead of an all-OK sweep."""
    watchlist = ["AAPL", "NVDA"]
    agent = await _seed_agent(db_session, watchlist=watchlist, approval_mode=ApprovalMode.REQUIRED)
    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=RuntimeError("LLM unreachable"))

    with pytest.raises(RuntimeError, match="LLM unreachable"):
        await run_watchlist_cycles(
            agent.id, db_session, llm_client=llm, collector=_collector_for(watchlist)
        )

    # Each attempted symbol still left an audited FAILED cycle.
    rows = (
        await db_session.execute(select(AgentCycle).where(AgentCycle.agent_id == agent.id))
    ).scalars().all()
    assert len(rows) == len(watchlist)
    assert all(r.status == CycleStatus.FAILED for r in rows)


@pytest.mark.asyncio
async def test_sweep_executes_each_symbol_in_auto_mode(db_session: AsyncSession):
    """AUTO mode: each per-symbol cycle runs the execution gate and, on approval,
    reaches COMPLETED with a submitted broker order for that symbol."""
    watchlist = ["AAPL", "NVDA", "MSFT"]
    agent = await _seed_agent(db_session, watchlist=watchlist, approval_mode=ApprovalMode.AUTO)

    allow = MagicMock()
    allow.decision = PolicyDecisionType.ALLOW
    allow.model_dump = MagicMock(return_value={"decision": "ALLOW", "evaluations": []})
    order = MagicMock()
    order.status.value = "SUBMITTED"
    gate = AsyncMock(return_value=(allow, order))

    with patch("app.agent.orchestrator.evaluate_and_submit_cycle_trade", gate):
        cycles = await run_watchlist_cycles(
            agent.id, db_session, llm_client=_echo_target_llm(), collector=_collector_for(watchlist)
        )

    assert gate.await_count == len(watchlist)
    assert [c.proposal_json["symbol"] for c in cycles] == watchlist
    assert all(c.status == CycleStatus.COMPLETED for c in cycles)


def test_prompt_has_no_hardcoded_symbol():
    prompt = get_system_prompt("S-001")
    # The schema example no longer pins a fixed ticker set.
    assert '"AAPL" | "NVDA" | "SPY"' not in prompt
    assert "AAPL" not in prompt
    # It drives the decision off the per-cycle target instead.
    assert "decision_target" in prompt
