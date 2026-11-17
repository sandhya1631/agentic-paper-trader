"""Recovery of cycles orphaned by a process that died mid-cycle (#185).

Every writer of an in-flight status (PENDING...VALIDATING, EXECUTING) holds the agent's
cycle lock, so an in-flight cycle whose agent lock is free belongs to a dead process:
a Render redeploy or crash mid-cycle. Recovery runs at API startup and inside the
active-cycle gate. These tests use the real Postgres advisory lock and row locks.
"""

import asyncio
import contextlib
import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from conftest import TestSessionLocal, test_engine
from httpx import AsyncClient
from sqlalchemy import select

import app.main as main_module
from app.agent import approval as approval_module
from app.agent import orchestrator
from app.agent import router as agent_router
from app.agent.orchestrator import CycleOverlapError, recover_orphaned_cycles, run_agent_cycle
from app.agent.orchestrator import agent_cycle_lock as real_agent_cycle_lock
from app.core.constants import CYCLE_INTERRUPTED
from app.db.enums import (
    ApprovalMode,
    ApprovalStatus,
    BrokerOrderStatus,
    CycleStatus,
    CycleTrigger,
    OrderSide,
    PolicyOutcome,
    UserRole,
)
from app.db.models import (
    AgentConfig,
    AgentCycle,
    BrokerOrder,
    CycleApproval,
    PolicySet,
    TradingAccount,
    User,
)
from app.schemas import PolicyDecision, PolicyEvaluation

PASSWORD = "S3cure-Password!"
HOLD = {"symbol": "AAPL", "action": "HOLD", "quantity": 0, "reasoning": "No setup."}
BUY = {"symbol": "AAPL", "action": "BUY", "quantity": 1, "reasoning": "RSI is 35."}


def _hold_llm() -> MagicMock:
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=json.dumps(HOLD))
    return llm


def _collector() -> MagicMock:
    collector = MagicMock()
    collector.fetch_latest_bars = AsyncMock(return_value={"AAPL": []})
    return collector


def _offline_cycle_deps(monkeypatch) -> None:
    """A manual or scheduled cycle with no network: HOLD model, no bars, no account."""
    llm = _hold_llm()
    monkeypatch.setattr(orchestrator, "get_llm_client", lambda: llm)
    monkeypatch.setattr(orchestrator, "create_market_data_collector", _collector)
    monkeypatch.setattr(orchestrator, "fetch_live_account_data", AsyncMock(return_value=None))


async def _login(client: AsyncClient, email: str) -> dict[str, str]:
    await client.post("/auth/register", json={"email": email, "password": PASSWORD})
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _seed_agent(
    *, email: str | None = None, approval_mode: ApprovalMode = ApprovalMode.REQUIRED
) -> uuid.UUID:
    """An agent for an OPERATOR; reuses the registered user when ``email`` is given."""
    async with TestSessionLocal() as db:
        if email is None:
            user = User(
                email=f"recovery-{uuid.uuid4().hex[:8]}@example.com",
                password_hash="x",
                role=UserRole.OPERATOR,
            )
            db.add(user)
            await db.flush()
        else:
            user = (await db.execute(select(User).where(User.email == email))).scalar_one()
            user.role = UserRole.OPERATOR
        account = TradingAccount(
            user_id=user.id,
            provider="ALPACA",
            encrypted_api_key="enc_key",
            encrypted_api_secret="enc_secret",
            is_paper=True,
            is_connected=True,
        )
        db.add(account)
        await db.flush()
        policy_set = PolicySet(version=f"P-{uuid.uuid4()}", limits_json={}, is_active=True)
        db.add(policy_set)
        await db.flush()
        agent = AgentConfig(
            account_id=account.id,
            policy_set_id=policy_set.id,
            name="Recovery agent",
            watchlist=["AAPL"],
            strategy_version="S-001",
            approval_mode=approval_mode,
            enabled=True,
            model_provider="ollama",
            model_name="llama3.1",
        )
        db.add(agent)
        await db.commit()
        return agent.id


async def _seed_cycle(
    agent_id: uuid.UUID,
    status: CycleStatus,
    *,
    trigger: CycleTrigger = CycleTrigger.MANUAL,
    failure_code: str | None = None,
    snapshot: dict | None = None,
) -> uuid.UUID:
    async with TestSessionLocal() as db:
        cycle = AgentCycle(
            agent_id=agent_id,
            trigger=trigger,
            status=status,
            started_at=datetime.now(UTC) - timedelta(minutes=5),
            failure_code=failure_code,
            snapshot_json=snapshot,
        )
        db.add(cycle)
        await db.commit()
        return cycle.id


async def _stored(cycle_id: uuid.UUID) -> AgentCycle:
    async with TestSessionLocal() as db:
        return (
            await db.execute(select(AgentCycle).where(AgentCycle.id == cycle_id))
        ).scalar_one()


async def _recover() -> list[tuple[uuid.UUID, CycleStatus, CycleStatus]]:
    async with TestSessionLocal() as db:
        return await recover_orphaned_cycles(db)


@contextlib.asynccontextmanager
async def _lock_held_elsewhere(agent_id: uuid.UUID):
    """Hold the agent's cycle lock on another connection, as a live process would."""
    async with TestSessionLocal() as other, real_agent_cycle_lock(other, agent_id):
        yield


# --- The issue's tests -------------------------------------------------------------


@pytest.mark.asyncio
async def test_orphaned_reasoning_cycle_fails_as_interrupted_and_next_manual_run_succeeds(
    client: AsyncClient, monkeypatch, caplog
):
    email = f"orphan-{uuid.uuid4().hex[:8]}@example.com"
    auth = await _login(client, email)
    agent_id = await _seed_agent(email=email)
    orphan_id = await _seed_cycle(agent_id, CycleStatus.REASONING)
    _offline_cycle_deps(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="app.agent.orchestrator"):
        response = await client.post(f"/agent/cycles/run?agent_id={agent_id}", headers=auth)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == CycleStatus.HOLD.value
    orphan = await _stored(orphan_id)
    assert orphan.status == CycleStatus.FAILED
    assert orphan.failure_code == CYCLE_INTERRUPTED == "INTERRUPTED"
    assert orphan.completed_at is not None
    assert any(
        str(orphan_id) in r.getMessage() and "REASONING -> FAILED" in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_orphaned_executing_cycle_is_reconciled_against_the_broker(
    db_session, monkeypatch
):
    agent_id = await _seed_agent(approval_mode=ApprovalMode.AUTO)
    orphan_id = await _seed_cycle(
        agent_id, CycleStatus.EXECUTING, snapshot={"execution_status": "EXECUTING"}
    )
    # The process died after the gateway persisted the order, before learning the outcome.
    async with TestSessionLocal() as db:
        db.add(
            BrokerOrder(
                decision_id=orphan_id,
                client_order_id=str(orphan_id),
                status=BrokerOrderStatus.PENDING_SUBMISSION,
                symbol="AAPL",
                side=OrderSide.BUY,
                requested_quantity=1,
            )
        )
        await db.commit()

    # Startup: EXECUTING -> RECONCILING, never resubmitted.
    assert await _recover() == [(orphan_id, CycleStatus.EXECUTING, CycleStatus.RECONCILING)]
    reconciling = await _stored(orphan_id)
    assert reconciling.status == CycleStatus.RECONCILING
    assert reconciling.failure_code == CYCLE_INTERRUPTED
    assert reconciling.snapshot_json["execution_status"] == "RECONCILIATION_REQUIRED"

    # The next run's gate looks the order up at Alpaca by client_order_id.
    alpaca = SimpleNamespace(
        get_order_by_client_order_id=AsyncMock(
            return_value=SimpleNamespace(id="alpaca-order-1", status="filled", filled_qty="1")
        ),
        submit_order=AsyncMock(),
    )

    class _Client:
        def __init__(self, **_):
            pass

        async def __aenter__(self):
            return alpaca

        async def __aexit__(self, *_):
            return None

    monkeypatch.setattr(
        approval_module,
        "get_decrypted_alpaca_credentials",
        AsyncMock(return_value=("paper-key", "paper-secret")),
    )
    monkeypatch.setattr(approval_module, "AlpacaClient", _Client)

    async with TestSessionLocal() as db:
        new_cycle = await run_agent_cycle(
            agent_id, db, llm_client=_hold_llm(), collector=_collector(), account_data=None
        )

    assert new_cycle.status == CycleStatus.HOLD
    alpaca.get_order_by_client_order_id.assert_awaited_once_with(str(orphan_id))
    alpaca.submit_order.assert_not_awaited()
    resolved = await _stored(orphan_id)
    assert resolved.status == CycleStatus.COMPLETED
    assert resolved.snapshot_json["execution_status"] == BrokerOrderStatus.FILLED.value


@pytest.mark.asyncio
async def test_cycle_whose_agent_lock_is_held_by_another_connection_is_untouched(
    db_session, caplog
):
    agent_id = await _seed_agent()
    reasoning_id = await _seed_cycle(agent_id, CycleStatus.REASONING)
    executing_id = await _seed_cycle(agent_id, CycleStatus.EXECUTING)

    with caplog.at_level(logging.INFO, logger="app.agent.orchestrator"):
        async with _lock_held_elsewhere(agent_id):
            assert await _recover() == []

    assert (await _stored(reasoning_id)).status == CycleStatus.REASONING
    assert (await _stored(executing_id)).status == CycleStatus.EXECUTING
    assert any("lock is held by a live process" in r.getMessage() for r in caplog.records)

    # Once that process is gone (its lock released), the same cycles are recovered.
    assert {cycle_id for cycle_id, *_ in await _recover()} == {reasoning_id, executing_id}


@pytest.mark.asyncio
async def test_startup_recovery_is_idempotent_and_skips_non_inflight_statuses(db_session):
    first_agent = await _seed_agent()
    second_agent = await _seed_agent()
    expected = {}
    for status in (CycleStatus.PENDING, CycleStatus.COLLECTING, CycleStatus.REASONING):
        expected[await _seed_cycle(first_agent, status)] = (status, CycleStatus.FAILED)
    for status, new in (
        (CycleStatus.VALIDATING, CycleStatus.FAILED),
        (CycleStatus.EXECUTING, CycleStatus.RECONCILING),
    ):
        expected[await _seed_cycle(second_agent, status)] = (status, new)
    untouched = {
        await _seed_cycle(first_agent, status, failure_code=code): (status, code)
        for status, code in (
            (CycleStatus.PENDING_APPROVAL, None),
            (CycleStatus.RECONCILING, "ORDER_EXECUTION_UNCERTAIN"),
            (CycleStatus.CANCELLED, "MANUAL_CYCLE_CANCELLED"),
            (CycleStatus.HOLD, None),
            (CycleStatus.FAILED, "LLM_OUTPUT_INVALID"),
            (CycleStatus.COMPLETED, None),
            (CycleStatus.REJECTED, "POLICY_DENIED"),
        )
    }

    first = await _recover()
    second = await _recover()

    assert {cycle_id: (old, new) for cycle_id, old, new in first} == expected
    assert second == []
    for cycle_id, (_old, new) in expected.items():
        cycle = await _stored(cycle_id)
        assert (cycle.status, cycle.failure_code) == (new, CYCLE_INTERRUPTED)
        assert (cycle.completed_at is not None) == (new == CycleStatus.FAILED)
    for cycle_id, (status, code) in untouched.items():
        cycle = await _stored(cycle_id)
        assert (cycle.status, cycle.failure_code) == (status, code)


@pytest.mark.asyncio
async def test_lifespan_recovers_before_the_scheduler_starts(db_session, monkeypatch):
    calls: list[str] = []

    async def recover(db):
        calls.append("recover")
        return []

    class _Scheduler:
        def start(self):
            calls.append("scheduler.start")

        def shutdown(self, wait):
            calls.append("scheduler.shutdown")

    monkeypatch.setattr(main_module, "engine", test_engine)
    monkeypatch.setattr(main_module, "AsyncSessionLocal", TestSessionLocal)
    monkeypatch.setattr(main_module, "recover_orphaned_cycles", recover)
    monkeypatch.setattr(main_module, "create_scheduler", lambda *a, **k: _Scheduler())

    async with main_module.lifespan(main_module.app):
        assert calls == ["recover", "scheduler.start"]


@pytest.mark.asyncio
async def test_lifespan_still_starts_when_recovery_fails(db_session, monkeypatch, caplog):
    started: list[bool] = []

    class _Scheduler:
        def start(self):
            started.append(True)

        def shutdown(self, wait):
            pass

    monkeypatch.setattr(main_module, "engine", test_engine)
    monkeypatch.setattr(main_module, "AsyncSessionLocal", TestSessionLocal)
    monkeypatch.setattr(
        main_module, "recover_orphaned_cycles", AsyncMock(side_effect=RuntimeError("db blip"))
    )
    monkeypatch.setattr(main_module, "create_scheduler", lambda *a, **k: _Scheduler())

    with caplog.at_level(logging.ERROR, logger="app.main"):
        async with main_module.lifespan(main_module.app):
            pass

    assert started == [True]
    assert any("Startup cycle recovery failed" in r.getMessage() for r in caplog.records)


# --- Approvals take the agent lock (the approval-path hazard) ------------------------


async def _park_approval(agent_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    proposal_hash = hashlib.sha256(json.dumps(BUY, sort_keys=True).encode()).hexdigest()
    async with TestSessionLocal() as db:
        cycle = AgentCycle(
            agent_id=agent_id,
            trigger=CycleTrigger.SCHEDULED,
            status=CycleStatus.PENDING_APPROVAL,
            started_at=datetime.now(UTC),
            proposal_json=BUY,
            snapshot_json={"execution_status": "PENDING_APPROVAL"},
        )
        db.add(cycle)
        await db.flush()
        approval = CycleApproval(
            cycle_id=cycle.id,
            status=ApprovalStatus.PENDING,
            proposal_hash=proposal_hash,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db.add(approval)
        await db.commit()
        return approval.id, cycle.id


def _allow() -> PolicyDecision:
    return PolicyDecision(
        decision="ALLOW",
        policy_version="POLICY-1.0",
        evaluated_at=datetime.now(UTC),
        evaluations=[
            PolicyEvaluation(
                policy_id="P-001",
                policy_name="Paper-only broker",
                outcome=PolicyOutcome.PASS,
                reason="OK",
                evidence={},
            )
        ],
        final_quantity=1,
        policy_token="signed-policy-token",
    )


@pytest.mark.asyncio
async def test_approval_is_refused_while_a_cycle_or_sweep_holds_the_agent_lock(
    client: AsyncClient, monkeypatch
):
    email = f"busy-{uuid.uuid4().hex[:8]}@example.com"
    auth = await _login(client, email)
    agent_id = await _seed_agent(email=email)
    approval_id, cycle_id = await _park_approval(agent_id)
    execute = AsyncMock(
        return_value=(_allow(), SimpleNamespace(status=BrokerOrderStatus.SUBMITTED))
    )
    monkeypatch.setattr(agent_router, "evaluate_and_submit_cycle_trade", execute)

    async with _lock_held_elsewhere(agent_id):  # e.g. a REQUIRED sweep still running
        busy = await client.post(
            f"/agent/approvals/{approval_id}/approve", headers=auth, json={"comment": "go"}
        )

    assert busy.status_code == 409
    assert busy.json()["detail"] == agent_router.APPROVAL_AGENT_BUSY_DETAIL
    execute.assert_not_awaited()
    async with TestSessionLocal() as db:
        approval = await db.get(CycleApproval, approval_id)
        assert approval.status == ApprovalStatus.PENDING  # nothing changed; retry works
    assert (await _stored(cycle_id)).status == CycleStatus.PENDING_APPROVAL

    retry = await client.post(
        f"/agent/approvals/{approval_id}/approve", headers=auth, json={"comment": "go"}
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["status"] == CycleStatus.COMPLETED.value
    execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_recovery_and_sweeps_never_touch_an_executing_approval(
    client: AsyncClient, monkeypatch
):
    email = f"race-{uuid.uuid4().hex[:8]}@example.com"
    auth = await _login(client, email)
    agent_id = await _seed_agent(email=email)
    approval_id, cycle_id = await _park_approval(agent_id)
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_submit(db, agent, cycle, *, submit=True):
        entered.set()
        await release.wait()  # the broker POST is in flight
        return _allow(), SimpleNamespace(status=BrokerOrderStatus.SUBMITTED)

    monkeypatch.setattr(agent_router, "evaluate_and_submit_cycle_trade", slow_submit)

    approve = asyncio.create_task(
        client.post(f"/agent/approvals/{approval_id}/approve", headers=auth, json={})
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        assert (await _stored(cycle_id)).status == CycleStatus.EXECUTING

        # A restarted instance's startup recovery sees the approval's lock and skips it.
        assert await _recover() == []
        # A manual run or sweep can't start beside it, so its gate can't recover it either.
        async with TestSessionLocal() as db:
            with pytest.raises(CycleOverlapError):
                await run_agent_cycle(
                    agent_id, db, llm_client=_hold_llm(), collector=_collector()
                )
        assert (await _stored(cycle_id)).status == CycleStatus.EXECUTING
    finally:
        release.set()
        response = await approve

    assert response.status_code == 200, response.text
    assert (await _stored(cycle_id)).status == CycleStatus.COMPLETED


# --- Recovery vs the cancel route --------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_waits_for_an_in_flight_cancel_and_leaves_it_cancelled(db_session):
    agent_id = await _seed_agent()
    cycle_id = await _seed_cycle(agent_id, CycleStatus.REASONING)

    async with TestSessionLocal() as canceller:
        # The cancel route's row lock: SELECT ... FOR UPDATE, then set CANCELLED.
        cycle = (
            await canceller.execute(
                select(AgentCycle).where(AgentCycle.id == cycle_id).with_for_update()
            )
        ).scalar_one()
        cycle.status = CycleStatus.CANCELLED
        cycle.failure_code = "MANUAL_CYCLE_CANCELLED"
        await canceller.flush()

        recovery = asyncio.create_task(_recover())
        await asyncio.sleep(0.3)
        assert not recovery.done()  # blocked on the cancel's row lock
        await canceller.commit()

    assert await asyncio.wait_for(recovery, timeout=10) == []
    cycle = await _stored(cycle_id)
    assert (cycle.status, cycle.failure_code) == (
        CycleStatus.CANCELLED,
        "MANUAL_CYCLE_CANCELLED",
    )


@pytest.mark.asyncio
async def test_cancel_after_recovery_is_refused(client: AsyncClient):
    email = f"cancel-{uuid.uuid4().hex[:8]}@example.com"
    auth = await _login(client, email)
    agent_id = await _seed_agent(email=email)
    cycle_id = await _seed_cycle(agent_id, CycleStatus.REASONING)

    assert await _recover() == [(cycle_id, CycleStatus.REASONING, CycleStatus.FAILED)]
    response = await client.post(f"/agent/cycles/{cycle_id}/cancel", headers=auth)

    assert response.status_code == 409
    assert (await _stored(cycle_id)).failure_code == CYCLE_INTERRUPTED


# --- Review follow-ups: a run that lost its lock, ordering, reconcile, robustness ------


@pytest.mark.asyncio
async def test_a_run_that_lost_its_lock_stops_once_recovery_closes_its_cycle(db_session):
    """If a run's lock connection drops mid-LLM-call, another process can take the lock
    and recover the run's cycle. The run must then stop at its next phase check: no
    proposal, no approval, nothing submitted."""
    agent_id = await _seed_agent()  # REQUIRED: a BUY would park an approval
    calls = 0

    async def generate(prompt_input, system=None):
        nonlocal calls
        calls += 1
        # Another process, holding the lock this run lost, recovers its cycle.
        async with TestSessionLocal() as other:
            await orchestrator._recover_orphaned_agent_cycles(other, agent_id)
        return json.dumps(BUY)

    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=generate)
    async with TestSessionLocal() as db:
        cycle = await run_agent_cycle(
            agent_id, db, llm_client=llm, collector=_collector(), account_data=None
        )

    assert calls == 1
    assert (cycle.status, cycle.failure_code) == (CycleStatus.FAILED, CYCLE_INTERRUPTED)
    stored = await _stored(cycle.id)
    assert (stored.status, stored.proposal_json) == (CycleStatus.FAILED, None)
    async with TestSessionLocal() as db:
        approvals = (await db.execute(select(CycleApproval))).scalars().all()
    assert approvals == []


@pytest.mark.asyncio
async def test_a_sweep_stops_when_another_process_recovers_its_cycle(db_session):
    agent_id = await _seed_agent(approval_mode=ApprovalMode.OBSERVE_ONLY)
    async with TestSessionLocal() as db:
        agent = await db.get(AgentConfig, agent_id)
        agent.watchlist = ["AAPL", "NVDA", "SPY"]
        await db.commit()
    calls = 0

    async def generate(prompt_input, system=None):
        nonlocal calls
        calls += 1
        async with TestSessionLocal() as other:
            await orchestrator._recover_orphaned_agent_cycles(other, agent_id)
        return json.dumps(HOLD)

    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=generate)
    collector = MagicMock()
    collector.fetch_latest_bars = AsyncMock(return_value={})
    async with TestSessionLocal() as db:
        cycles = await orchestrator.run_watchlist_cycles(
            agent_id, db, llm_client=llm, collector=collector
        )

    # The first symbol's cycle was taken over, so the sweep ran no more symbols.
    assert calls == 1
    assert [(c.status, c.failure_code) for c in cycles] == [
        (CycleStatus.FAILED, CYCLE_INTERRUPTED)
    ]
    async with TestSessionLocal() as db:
        rows = (
            await db.execute(select(AgentCycle).where(AgentCycle.agent_id == agent_id))
        ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_gate_recovers_an_orphan_even_behind_a_parked_approval(db_session):
    agent_id = await _seed_agent()
    parked_id = await _seed_cycle(agent_id, CycleStatus.PENDING_APPROVAL)
    orphan_id = await _seed_cycle(agent_id, CycleStatus.REASONING)

    async with TestSessionLocal() as db:
        with pytest.raises(CycleOverlapError, match=str(parked_id)):
            await run_agent_cycle(agent_id, db, llm_client=_hold_llm(), collector=_collector())

    orphan = await _stored(orphan_id)
    assert (orphan.status, orphan.failure_code) == (CycleStatus.FAILED, CYCLE_INTERRUPTED)
    assert (await _stored(parked_id)).status == CycleStatus.PENDING_APPROVAL


@pytest.mark.asyncio
async def test_executing_orphan_without_a_broker_order_records_its_outcome(db_session):
    """Died after the EXECUTING commit, before the gateway persisted an order."""
    agent_id = await _seed_agent(approval_mode=ApprovalMode.AUTO)
    orphan_id = await _seed_cycle(
        agent_id, CycleStatus.EXECUTING, snapshot={"execution_status": "EXECUTING"}
    )

    async with TestSessionLocal() as db:
        new_cycle = await run_agent_cycle(
            agent_id, db, llm_client=_hold_llm(), collector=_collector(), account_data=None
        )

    assert new_cycle.status == CycleStatus.HOLD
    orphan = await _stored(orphan_id)
    assert (orphan.status, orphan.failure_code) == (
        CycleStatus.FAILED,
        "ORDER_SUBMISSION_NOT_STARTED",
    )
    assert orphan.snapshot_json["execution_status"] == "ORDER_SUBMISSION_NOT_STARTED"
    assert orphan.completed_at is not None


@pytest.mark.asyncio
async def test_startup_recovery_continues_past_an_agent_whose_recovery_fails(
    db_session, monkeypatch
):
    broken_agent = await _seed_agent()
    healthy_agent = await _seed_agent()
    await _seed_cycle(broken_agent, CycleStatus.REASONING)
    healthy_orphan = await _seed_cycle(healthy_agent, CycleStatus.COLLECTING)
    real_recover = orchestrator._recover_orphaned_agent_cycles

    async def recover(db, agent_id):
        if agent_id == broken_agent:
            raise RuntimeError("connection dropped")
        return await real_recover(db, agent_id)

    monkeypatch.setattr(orchestrator, "_recover_orphaned_agent_cycles", recover)
    async with TestSessionLocal() as db:

        async def broken_rollback():
            raise RuntimeError("rollback on a dead connection")

        monkeypatch.setattr(db, "rollback", broken_rollback)
        recovered = await recover_orphaned_cycles(db)

    assert recovered == [(healthy_orphan, CycleStatus.COLLECTING, CycleStatus.FAILED)]
