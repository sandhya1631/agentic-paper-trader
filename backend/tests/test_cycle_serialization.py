"""Per-agent cycle serialization (#156).

A manual run (API process) and a scheduled watchlist sweep (Celery worker) for the
same agent must never run concurrently: the old check-then-insert status gate let
both pass before either had inserted a cycle row, and in AUTO mode both could submit
an order for the same symbol. Each test parks one runner while it holds the agent's
lock but before it has created any AgentCycle row (the window the status gate cannot
see), so these tests fail without the lock.
"""

import asyncio
import logging
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from conftest import TestSessionLocal
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from test_multi_symbol_cycle import (
    _collector_for,
    _echo_target_llm,
    _fixed_symbol_llm,
    _make_bar,
    _seed_agent,
)

from app.agent import orchestrator
from app.agent.orchestrator import (
    CycleOverlapError,
    run_agent_cycle,
    run_watchlist_cycles,
)
from app.agent.orchestrator import agent_cycle_lock as real_agent_cycle_lock
from app.db.enums import ApprovalMode, CycleTrigger, PolicyDecisionType, UserRole
from app.db.models import AgentCycle, TradingAccount, User

WATCHLIST = ["AAPL", "NVDA"]


def _recording_submit(monkeypatch) -> list[str]:
    """Stand in for the policy gate + broker: ALLOW every proposal and record its symbol."""
    submitted: list[str] = []
    allow = MagicMock()
    allow.decision = PolicyDecisionType.ALLOW
    allow.model_dump = MagicMock(return_value={"decision": "ALLOW", "evaluations": []})
    order = MagicMock()
    order.status.value = "SUBMITTED"

    async def submit(db, agent, cycle, *args, **kwargs):
        submitted.append(cycle.proposal_json["symbol"])
        return allow, order

    monkeypatch.setattr(orchestrator, "evaluate_and_submit_cycle_trade", submit)
    return submitted


def _blocking_collector(entered: asyncio.Event, release: asyncio.Event) -> MagicMock:
    """A collector whose fetch parks the sweep in its bar prefetch (lock held, no rows yet)."""
    bars = {s: [_make_bar(150.0) for _ in range(50)] for s in WATCHLIST}

    async def fetch(*args, **kwargs):
        entered.set()
        await release.wait()
        return bars

    collector = MagicMock()
    collector.fetch_latest_bars = AsyncMock(side_effect=fetch)
    return collector


async def _cycles(trigger: CycleTrigger, agent_id: uuid.UUID) -> list[AgentCycle]:
    async with TestSessionLocal() as db:
        rows = await db.execute(
            select(AgentCycle).where(
                AgentCycle.agent_id == agent_id, AgentCycle.trigger == trigger
            )
        )
        return list(rows.scalars().all())


@pytest.mark.asyncio
async def test_manual_run_and_watchlist_sweep_cannot_both_submit_same_symbol(
    db_session: AsyncSession, monkeypatch
):
    agent = await _seed_agent(db_session, watchlist=WATCHLIST, approval_mode=ApprovalMode.AUTO)
    submitted = _recording_submit(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()

    sweep = asyncio.create_task(
        run_watchlist_cycles(
            agent.id,
            db_session,
            llm_client=_echo_target_llm(),
            collector=_blocking_collector(entered, release),
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
        # The sweep holds the lock but has not created a cycle row yet, so the old
        # status gate would have let this manual AAPL order through as well.
        async with TestSessionLocal() as other:
            with pytest.raises(CycleOverlapError):
                await run_agent_cycle(
                    agent.id,
                    other,
                    llm_client=_fixed_symbol_llm("AAPL"),
                    collector=_collector_for(WATCHLIST),
                )
    finally:
        release.set()
        cycles = await sweep

    assert [c.proposal_json["symbol"] for c in cycles] == WATCHLIST
    assert sorted(submitted) == ["AAPL", "NVDA"]  # AAPL exactly once
    assert await _cycles(CycleTrigger.MANUAL, agent.id) == []


@pytest.mark.asyncio
async def test_watchlist_sweep_reports_overlap_while_manual_cycle_holds_the_lock(
    db_session: AsyncSession, monkeypatch
):
    agent = await _seed_agent(db_session, watchlist=WATCHLIST, approval_mode=ApprovalMode.AUTO)
    submitted = _recording_submit(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()

    real_expire = orchestrator.expire_pending_approvals
    calls = 0

    async def parked_expire(db, agent_id):
        # The manual run's first gate step: lock held, cycle row not inserted yet.
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
        return await real_expire(db, agent_id)

    monkeypatch.setattr(orchestrator, "expire_pending_approvals", parked_expire)

    manual = asyncio.create_task(
        run_agent_cycle(
            agent.id,
            db_session,
            llm_client=_fixed_symbol_llm("AAPL"),
            collector=_collector_for(WATCHLIST),
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
        async with TestSessionLocal() as other:
            with pytest.raises(CycleOverlapError):
                await run_watchlist_cycles(
                    agent.id,
                    other,
                    llm_client=_echo_target_llm(),
                    collector=_collector_for(WATCHLIST),
                )
        assert await _cycles(CycleTrigger.SCHEDULED, agent.id) == []
    finally:
        release.set()
        await manual

    assert submitted == ["AAPL"]


@pytest.mark.asyncio
async def test_manual_trigger_returns_409_while_agent_lock_is_held(client: AsyncClient):
    email = f"lock-http-{uuid.uuid4()}@example.com"
    password = "S3cure-Password!"
    await client.post("/auth/register", json={"email": email, "password": password})
    login = await client.post("/auth/login", json={"email": email, "password": password})
    token = login.json()["access_token"]

    async with TestSessionLocal() as db:
        agent = await _seed_agent(db, watchlist=WATCHLIST, approval_mode=ApprovalMode.REQUIRED)
        owner = (await db.execute(select(User).where(User.email == email))).scalar_one()
        owner.role = UserRole.OPERATOR  # running cycles is operator-only (#176)
        account = await db.get(TradingAccount, agent.account_id)
        account.user_id = owner.id  # hand the seeded agent to the logged-in user
        await db.commit()

        # Another process (e.g. a scheduled sweep) holds this agent's lock.
        async with real_agent_cycle_lock(db, agent.id):
            response = await client.post(
                f"/agent/cycles/run?agent_id={agent.id}",
                headers={"Authorization": f"Bearer {token}"},
            )

    assert response.status_code == 409
    assert await _cycles(CycleTrigger.MANUAL, agent.id) == []


@pytest.mark.asyncio
async def test_agent_cycle_lock_is_per_agent_and_released_on_exit():
    first, second = uuid.uuid4(), uuid.uuid4()
    async with TestSessionLocal() as a, TestSessionLocal() as b:
        async with real_agent_cycle_lock(a, first):
            with pytest.raises(CycleOverlapError):
                async with real_agent_cycle_lock(b, first):
                    pass
            async with real_agent_cycle_lock(b, second):  # other agents are unaffected
                pass

        async with real_agent_cycle_lock(b, first):  # released after a normal exit
            pass

        with pytest.raises(RuntimeError, match="boom"):
            async with real_agent_cycle_lock(a, first):
                raise RuntimeError("boom")
        async with real_agent_cycle_lock(b, first):  # released after an exception
            pass


@pytest.mark.asyncio
async def test_agent_cycle_lock_fails_closed_without_a_real_engine():
    with pytest.raises(RuntimeError, match="AsyncEngine"):
        async with real_agent_cycle_lock(MagicMock(), uuid.uuid4()):
            pass


@pytest.mark.asyncio
async def test_sweep_evaluates_each_symbol_once(db_session: AsyncSession):
    agent = await _seed_agent(
        db_session,
        watchlist=["AAPL", "aapl", " NVDA ", "AAPL"],
        approval_mode=ApprovalMode.REQUIRED,
    )

    cycles = await run_watchlist_cycles(
        agent.id, db_session, llm_client=_echo_target_llm(), collector=_collector_for(WATCHLIST)
    )

    assert [c.proposal_json["symbol"] for c in cycles] == WATCHLIST
    # Each per-symbol cycle's context lists every distinct symbol once, too.
    assert all(c.snapshot_json["watchlist"] == WATCHLIST for c in cycles)


@pytest.mark.asyncio
async def test_manual_cycle_collects_each_normalized_symbol_once(db_session: AsyncSession):
    """A manual (untargeted) cycle normalizes the watchlist the same way the sweep does:
    market data is fetched, and the context built, for each distinct symbol exactly once."""
    agent = await _seed_agent(
        db_session,
        watchlist=["AAPL", "aapl", "NVDA", " nvda ", "SPY", "  "],
        approval_mode=ApprovalMode.OBSERVE_ONLY,
    )
    normalized = ["AAPL", "NVDA", "SPY"]
    collector = _collector_for(normalized)

    cycle = await run_agent_cycle(
        agent.id, db_session, llm_client=_fixed_symbol_llm("AAPL"), collector=collector
    )

    collector.fetch_latest_bars.assert_awaited_once()
    assert collector.fetch_latest_bars.await_args.args[0] == normalized
    assert cycle.snapshot_json["watchlist"] == normalized
    assert list(cycle.snapshot_json["market"]) == normalized


@pytest.mark.asyncio
async def test_lost_lock_connection_does_not_fail_a_finished_run(caplog):
    """If the lock's connection drops mid-run (e.g. a Postgres restart), the run that
    already finished must not be reported as failed: Postgres has released the lock
    with the connection, so the release error is only logged."""
    agent_id = uuid.uuid4()
    async with TestSessionLocal() as db:
        with caplog.at_level(logging.WARNING, logger="app.agent.orchestrator"):
            async with real_agent_cycle_lock(db, agent_id):
                # Kill the backend holding the advisory lock, as a DB restart would.
                killed = await db.scalar(
                    text(
                        "SELECT count(pg_terminate_backend(pid)) FROM pg_locks "
                        "WHERE locktype = 'advisory' AND pid <> pg_backend_pid()"
                    )
                )
                assert killed == 1
        # Leaving the block did not raise; the lost connection was logged instead.
        assert any("cycle-lock connection was lost" in r.message for r in caplog.records)

        # The lock is free again for the next run.
        async with real_agent_cycle_lock(db, agent_id):
            pass
