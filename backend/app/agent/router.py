"""Agent Router Endpoints (Story 2.3.2 / Issue #28).

Exposes HTTP endpoints for triggering manual agent cycles and querying cycle details.
"""

import hashlib
import hmac
import json
import uuid
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agent import scheduler as scheduler_service
from app.agent.approval import evaluate_and_submit_cycle_trade
from app.agent.orchestrator import CycleOverlapError, run_agent_cycle
from app.auth.dependencies import get_current_user
from app.broker.gateway import TradingDisabledError
from app.core.config import get_settings
from app.core.constants import ALPACA_PROVIDER, DEFAULT_STRATEGY_VERSION, DEFAULT_WATCHLIST
from app.db.enums import (
    ApprovalStatus,
    BrokerOrderStatus,
    CycleStatus,
    CycleTrigger,
    UserRole,
)
from app.db.models import (
    AgentConfig,
    AgentCycle,
    AgentSchedule,
    BrokerOrder,
    CycleApproval,
    PolicySet,
    TradingAccount,
    User,
)
from app.db.session import get_db
from app.risk.kill_switch import (
    EnvironmentKillSwitchOverrideError,
    get_kill_switch_control,
    set_kill_switch,
)
from app.schemas import (
    AgentScheduleRead,
    ApprovalAction,
    ApprovalRead,
    CycleRead,
    KillSwitchRead,
    KillSwitchUpdate,
    PendingApprovalRead,
    ScheduledCycleStatus,
    ScheduledOrderStatus,
    SchedulerLogsRead,
    SchedulerStatusRead,
    SchedulerTickLog,
    ScheduleStartRead,
    ScheduleStartRequest,
)

router = APIRouter(prefix="/agent", tags=["agent"])


@router.get(
    "/kill-switch",
    response_model=KillSwitchRead,
    summary="Get global trading kill-switch state (admin only)",
)
async def get_kill_switch(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> KillSwitchRead:
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin role required")
    settings = get_settings()
    control = await get_kill_switch_control(db)
    return KillSwitchRead(
        enabled=settings.kill_switch_enabled
        or bool(control and control.kill_switch_enabled),
        enforced_by_environment=settings.kill_switch_enabled,
        updated_at=control.updated_at if control else None,
        updated_by=control.updated_by if control else None,
    )


@router.put(
    "/kill-switch",
    response_model=KillSwitchRead,
    summary="Enable or disable the global trading kill switch (admin only)",
)
async def update_kill_switch(
    body: KillSwitchUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> KillSwitchRead:
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin role required")
    try:
        control = await set_kill_switch(
            db,
            enabled=body.enabled,
            actor_id=current_user.id,
        )
    except EnvironmentKillSwitchOverrideError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    settings = get_settings()
    return KillSwitchRead(
        enabled=settings.kill_switch_enabled or control.kill_switch_enabled,
        enforced_by_environment=settings.kill_switch_enabled,
        updated_at=control.updated_at,
        updated_by=control.updated_by,
    )


@router.post("/cycles/run", response_model=CycleRead, summary="Trigger manual agent cycle")
async def trigger_manual_cycle(
    agent_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Manually trigger one execution cycle for an agent.

    Auto-creates a default agent if none exists.
    """
    if agent_id is None:
        stmt = (
            select(AgentConfig)
            .join(TradingAccount, AgentConfig.account_id == TradingAccount.id)
            .where(TradingAccount.user_id == current_user.id)
            .limit(1)
        )
        res = await db.execute(stmt)
        agent = res.scalar_one_or_none()
        if not agent:
            # Auto-provision default TradingAccount, PolicySet, and AgentConfig for instant testing
            acc_stmt = select(TradingAccount).where(TradingAccount.user_id == current_user.id)
            acc_res = await db.execute(acc_stmt)
            account = acc_res.scalar_one_or_none()
            if not account:
                account = TradingAccount(
                    user_id=current_user.id,
                    provider=ALPACA_PROVIDER,
                    encrypted_api_key="demo_encrypted_key",
                    encrypted_api_secret="demo_encrypted_secret",
                    is_paper=True,
                    is_connected=True,
                )
                db.add(account)
                await db.flush()

            pol_stmt = select(PolicySet).where(PolicySet.is_active).limit(1)
            pol_res = await db.execute(pol_stmt)
            policy_set = pol_res.scalar_one_or_none()
            if not policy_set:
                policy_set = PolicySet(version="P-DEFAULT", limits_json={}, is_active=True)
                db.add(policy_set)
                await db.flush()

            from app.db.enums import ApprovalMode
            agent = AgentConfig(
                account_id=account.id,
                policy_set_id=policy_set.id,
                name="Default Momentum Agent",
                watchlist=list(DEFAULT_WATCHLIST),
                strategy_version=DEFAULT_STRATEGY_VERSION,
                cadence_minutes=15,
                approval_mode=ApprovalMode.REQUIRED,
                enabled=True,
                model_provider="ollama",
                model_name="llama3.1",
            )
            db.add(agent)
            await db.commit()
            await db.refresh(agent)

        target_agent_id = agent.id
    else:
        agent_result = await db.execute(
            select(AgentConfig)
            .join(TradingAccount, AgentConfig.account_id == TradingAccount.id)
            .where(
                AgentConfig.id == agent_id,
                TradingAccount.user_id == current_user.id,
            )
        )
        agent = agent_result.scalar_one_or_none()
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found")
        target_agent_id = agent.id

    try:
        cycle = await run_agent_cycle(
            target_agent_id, db, trigger=CycleTrigger.MANUAL
        )
        return cycle
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except CycleOverlapError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent cycle failed: {exc}",
        ) from exc


@router.get("/cycles/{cycle_id}", response_model=CycleRead, summary="Retrieve agent cycle status")
async def get_cycle_status(
    cycle_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Retrieve details and proposal status of a specific agent cycle."""
    stmt = (
        select(AgentCycle)
        .join(AgentConfig, AgentCycle.agent_id == AgentConfig.id)
        .join(TradingAccount, AgentConfig.account_id == TradingAccount.id)
        .options(selectinload(AgentCycle.approval))
        .where(
            AgentCycle.id == cycle_id,
            TradingAccount.user_id == current_user.id,
        )
    )
    res = await db.execute(stmt)
    cycle = res.scalar_one_or_none()
    if not cycle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"AgentCycle '{cycle_id}' not found.",
        )
    return cycle


def _job_id(schedule_id: uuid.UUID) -> str:
    return f"agent-schedule-{schedule_id}"


def _schedule_read(
    schedule: AgentSchedule,
    scheduler_instance,
    *,
    user_enabled: bool = True,
) -> AgentScheduleRead:
    job = (
        scheduler_instance.get_job(_job_id(schedule.id))
        if scheduler_instance is not None
        else None
    )
    return AgentScheduleRead(
        id=schedule.id,
        agent_id=schedule.agent_id,
        interval_minutes=schedule.interval_minutes,
        enabled=schedule.enabled and user_enabled,
        next_run_time=job.next_run_time if job else None,
        last_tick=scheduler_service.get_last_tick_status(schedule.id),
    )


async def _load_owned_approval(db: AsyncSession, user_id: uuid.UUID, approval_id):
    result = await db.execute(
        select(CycleApproval, AgentCycle, AgentConfig)
        .join(AgentCycle, CycleApproval.cycle_id == AgentCycle.id)
        .join(AgentConfig, AgentCycle.agent_id == AgentConfig.id)
        .join(TradingAccount, AgentConfig.account_id == TradingAccount.id)
        .where(
            CycleApproval.id == approval_id,
            TradingAccount.user_id == user_id,
        )
        .with_for_update()
    )
    return result.one_or_none()


@router.get(
    "/approvals/pending",
    response_model=list[PendingApprovalRead],
    summary="List pending cycle trade approvals",
)
async def list_pending_approvals(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[PendingApprovalRead]:
    result = await db.execute(
        select(CycleApproval, AgentCycle.proposal_json)
        .join(AgentCycle, CycleApproval.cycle_id == AgentCycle.id)
        .join(AgentConfig, AgentCycle.agent_id == AgentConfig.id)
        .join(TradingAccount, AgentConfig.account_id == TradingAccount.id)
        .where(
            TradingAccount.user_id == current_user.id,
            CycleApproval.status == ApprovalStatus.PENDING,
        )
        .order_by(CycleApproval.requested_at.asc())
    )
    return [
        PendingApprovalRead(
            approval=ApprovalRead.model_validate(approval),
            proposal=proposal,
        )
        for approval, proposal in result.all()
        if proposal is not None
    ]


@router.post(
    "/approvals/{approval_id}/approve",
    response_model=CycleRead,
    summary="Approve and execute a policy-checked paper order",
)
async def approve_cycle_trade(
    approval_id: uuid.UUID,
    body: ApprovalAction,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CycleRead:
    row = await _load_owned_approval(db, current_user.id, approval_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    approval, cycle, agent = row
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=409, detail="Approval is no longer pending")

    now = datetime.now(UTC)
    expires_at = approval.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= now:
        approval.status = ApprovalStatus.EXPIRED
        approval.acted_at = now
        cycle.status = CycleStatus.REJECTED
        cycle.failure_code = "APPROVAL_EXPIRED"
        cycle.completed_at = now
        cycle.snapshot_json = {
            **(cycle.snapshot_json or {}),
            "execution_status": "APPROVAL_EXPIRED",
        }
        await db.commit()
        raise HTTPException(status_code=409, detail="Approval has expired")

    if cycle.proposal_json is None:
        raise HTTPException(status_code=409, detail="Cycle has no proposal to approve")
    proposal_hash = hashlib.sha256(
        json.dumps(cycle.proposal_json, sort_keys=True).encode()
    ).hexdigest()
    if not hmac.compare_digest(approval.proposal_hash, proposal_hash):
        raise HTTPException(status_code=409, detail="Cycle proposal no longer matches approval")

    approval.status = ApprovalStatus.APPROVED
    approval.actor_id = current_user.id
    approval.acted_at = now
    approval.comment = body.comment
    cycle.status = CycleStatus.EXECUTING
    await db.commit()
    try:
        policy_decision, broker_order = await evaluate_and_submit_cycle_trade(
            db,
            agent,
            cycle,
        )
    except TradingDisabledError as exc:
        approval.status = ApprovalStatus.REJECTED
        approval.comment = "Execution blocked by the global kill switch"
        cycle.status = CycleStatus.REJECTED
        cycle.failure_code = "KILL_SWITCH_ENABLED"
        cycle.failure_message = str(exc)
        cycle.completed_at = datetime.now(UTC)
        cycle.snapshot_json = {
            **(cycle.snapshot_json or {}),
            "execution_status": "KILL_SWITCH_ENABLED",
        }
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Order execution is blocked by the global kill switch",
        ) from exc
    except Exception as exc:
        approval.status = ApprovalStatus.APPROVED
        approval.actor_id = current_user.id
        approval.acted_at = now
        approval.comment = body.comment or "Execution result uncertain; reconciliation required"
        cycle.status = CycleStatus.RECONCILING
        cycle.failure_code = "ORDER_EXECUTION_UNCERTAIN"
        cycle.failure_message = str(exc)
        cycle.snapshot_json = {
            **(cycle.snapshot_json or {}),
            "execution_status": "RECONCILIATION_REQUIRED",
        }
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Order execution could not be confirmed; reconcile before retrying",
        ) from exc

    approval.policy_version = policy_decision.policy_version
    approval.policy_result_json = policy_decision.model_dump(mode="json")
    approval.acted_at = now
    approval.actor_id = current_user.id
    approval.comment = body.comment
    cycle.completed_at = now
    snapshot = dict(cycle.snapshot_json or {})
    snapshot["policy_evaluation"] = policy_decision.model_dump(mode="json")
    if broker_order is None:
        approval.status = ApprovalStatus.REJECTED
        approval.comment = (
            f"{body.comment}; risk policies denied this proposal"
            if body.comment
            else "Risk policies denied this proposal"
        )
        cycle.status = CycleStatus.REJECTED
        cycle.failure_code = "POLICY_DENIED"
        snapshot["execution_status"] = "POLICY_DENIED"
    elif broker_order.status == BrokerOrderStatus.REJECTED:
        approval.status = ApprovalStatus.APPROVED
        cycle.status = CycleStatus.REJECTED
        cycle.failure_code = "BROKER_REJECTED"
        cycle.failure_message = broker_order.rejection_reason
        snapshot["execution_status"] = "ORDER_REJECTED"
    else:
        approval.status = ApprovalStatus.APPROVED
        cycle.status = CycleStatus.COMPLETED
        snapshot["execution_status"] = "ORDER_SUBMITTED"
    cycle.snapshot_json = snapshot
    await db.commit()
    await db.refresh(cycle)
    cycle.approval = approval
    return cycle


@router.post(
    "/approvals/{approval_id}/reject",
    response_model=CycleRead,
    summary="Reject a pending cycle trade approval",
)
async def reject_cycle_trade(
    approval_id: uuid.UUID,
    body: ApprovalAction,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CycleRead:
    row = await _load_owned_approval(db, current_user.id, approval_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    approval, cycle, _agent = row
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=409, detail="Approval is no longer pending")

    now = datetime.now(UTC)
    approval.status = ApprovalStatus.REJECTED
    approval.acted_at = now
    approval.actor_id = current_user.id
    approval.comment = body.comment
    cycle.status = CycleStatus.REJECTED
    cycle.completed_at = now
    cycle.snapshot_json = {
        **(cycle.snapshot_json or {}),
        "execution_status": "HUMAN_REJECTED",
    }
    await db.commit()
    await db.refresh(cycle)
    cycle.approval = approval
    return cycle


@router.post(
    "/scheduler/start",
    response_model=ScheduleStartRead,
    status_code=status.HTTP_201_CREATED,
    summary="Start a recurring schedule and resume scheduling for this user",
)
async def start_agent_schedule(
    body: ScheduleStartRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ScheduleStartRead:
    """Persist one schedule, resume this user, and restore all their enabled schedules."""
    scheduler_instance = getattr(request.app.state, "scheduler", None)
    if scheduler_instance is None or not scheduler_instance.running:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scheduler service is not running",
        )

    agent_result = await db.execute(
        select(AgentConfig)
        .join(TradingAccount, AgentConfig.account_id == TradingAccount.id)
        .where(
            AgentConfig.id == body.agent_id,
            TradingAccount.user_id == current_user.id,
        )
    )
    agent = agent_result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not agent.enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Enable this agent before scheduling cycles",
        )

    existing_result = await db.execute(
        select(AgentSchedule).where(
            AgentSchedule.user_id == current_user.id,
            AgentSchedule.agent_id == agent.id,
        )
    )
    schedule = existing_result.scalar_one_or_none()
    if schedule is None:
        schedule = AgentSchedule(
            user_id=current_user.id,
            agent_id=agent.id,
            interval_minutes=body.interval_minutes,
            enabled=True,
        )
        db.add(schedule)
    else:
        schedule.interval_minutes = body.interval_minutes
        schedule.enabled = True
    current_user.scheduler_enabled = True
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing_result = await db.execute(
            select(AgentSchedule).where(
                AgentSchedule.user_id == current_user.id,
                AgentSchedule.agent_id == agent.id,
            )
        )
        schedule = existing_result.scalar_one_or_none()
        if schedule is None:
            raise
        schedule.interval_minutes = body.interval_minutes
        schedule.enabled = True
        current_user.scheduler_enabled = True
        await db.commit()
    await db.refresh(schedule)
    schedules_result = await db.execute(
        select(AgentSchedule).where(
            AgentSchedule.user_id == current_user.id,
            AgentSchedule.enabled.is_(True),
        )
    )
    for active_schedule in schedules_result.scalars().all():
        scheduler_service.add_agent_schedule_job(
            scheduler_instance,
            active_schedule.id,
            active_schedule.interval_minutes,
        )
    return ScheduleStartRead(
        schedule=_schedule_read(schedule, scheduler_instance),
        status_url="/agent/scheduler/status",
    )


@router.post(
    "/scheduler/{schedule_id}/stop",
    response_model=AgentScheduleRead,
    summary="Pause scheduled processing for this user",
)
async def stop_agent_schedule(
    schedule_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AgentScheduleRead:
    """Pause all scheduled cycles for the owning user; in-flight cycles may finish."""
    result = await db.execute(
        select(AgentSchedule).where(
            AgentSchedule.id == schedule_id,
            AgentSchedule.user_id == current_user.id,
        )
    )
    schedule = result.scalar_one_or_none()
    if schedule is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    current_user.scheduler_enabled = False
    await db.commit()
    await db.refresh(schedule)

    scheduler_instance = getattr(request.app.state, "scheduler", None)
    if scheduler_instance is not None:
        schedules_result = await db.execute(
            select(AgentSchedule.id).where(
                AgentSchedule.user_id == current_user.id,
            )
        )
        for owned_schedule_id in schedules_result.scalars().all():
            job_id = _job_id(owned_schedule_id)
            if scheduler_instance.get_job(job_id) is not None:
                scheduler_instance.remove_job(job_id)
    return _schedule_read(
        schedule,
        scheduler_instance,
        user_enabled=current_user.scheduler_enabled,
    )


@router.get(
    "/scheduler/status",
    response_model=SchedulerStatusRead,
    summary="Check scheduler and recent scheduled cycle status",
)
async def get_scheduler_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SchedulerStatusRead:
    """Report this user's scheduler state and recent scheduled cycles and orders."""
    settings = get_settings()
    scheduler_instance = getattr(request.app.state, "scheduler", None)
    job = (
        scheduler_instance.get_job("scheduled-agent-cycles")
        if scheduler_instance is not None
        else None
    )
    schedules_result = await db.execute(
        select(AgentSchedule)
        .where(AgentSchedule.user_id == current_user.id)
        .order_by(AgentSchedule.created_at.desc())
    )
    schedules = schedules_result.scalars().all()

    cycles_result = await db.execute(
        select(AgentCycle)
        .join(AgentConfig, AgentCycle.agent_id == AgentConfig.id)
        .join(TradingAccount, AgentConfig.account_id == TradingAccount.id)
        .options(selectinload(AgentCycle.approval))
        .where(
            TradingAccount.user_id == current_user.id,
            AgentCycle.trigger == CycleTrigger.SCHEDULED,
        )
        .order_by(AgentCycle.started_at.desc())
        .limit(10)
    )
    cycles = cycles_result.scalars().all()
    cycle_ids = [cycle.id for cycle in cycles]

    orders_by_cycle: dict[uuid.UUID, list[ScheduledOrderStatus]] = defaultdict(list)
    if cycle_ids:
        orders_result = await db.execute(
            select(BrokerOrder)
            .where(BrokerOrder.decision_id.in_(cycle_ids))
            .order_by(BrokerOrder.created_at.desc())
        )
        for order in orders_result.scalars().all():
            orders_by_cycle[order.decision_id].append(
                ScheduledOrderStatus(
                    id=order.id,
                    status=order.status,
                    symbol=order.symbol,
                    side=order.side,
                    requested_quantity=order.requested_quantity,
                    filled_quantity=order.filled_quantity,
                    average_fill_price=order.average_fill_price,
                )
            )

    recent_cycles = []
    for cycle in cycles:
        orders = orders_by_cycle[cycle.id]
        if not orders:
            if cycle.status in {
                CycleStatus.PENDING,
                CycleStatus.COLLECTING,
                CycleStatus.REASONING,
                CycleStatus.VALIDATING,
                CycleStatus.EXECUTING,
            }:
                execution_status = "RUNNING"
            elif (
                cycle.approval is not None
                and cycle.approval.status == ApprovalStatus.PENDING
            ):
                execution_status = "PENDING_APPROVAL"
            elif cycle.snapshot_json and cycle.snapshot_json.get("execution_status"):
                execution_status = cycle.snapshot_json["execution_status"]
            elif cycle.status == CycleStatus.REJECTED:
                execution_status = "REJECTED"
            else:
                execution_status = "NO_ORDER"
        elif all(order.status.value == "FILLED" for order in orders):
            execution_status = "FILLED"
        elif any(order.status.value == "PARTIALLY_FILLED" for order in orders):
            execution_status = "PARTIALLY_FILLED"
        elif all(order.status.value == "REJECTED" for order in orders):
            execution_status = "REJECTED"
        else:
            execution_status = "ORDER_EXISTS"
        recent_cycles.append(
            ScheduledCycleStatus(
                cycle_id=cycle.id,
                agent_id=cycle.agent_id,
                status=cycle.status,
                started_at=cycle.started_at,
                completed_at=cycle.completed_at,
                proposal=cycle.proposal_json,
                execution_status=execution_status,
                approval=(
                    ApprovalRead.model_validate(cycle.approval)
                    if cycle.approval is not None
                    else None
                ),
                orders=orders,
            )
        )

    return SchedulerStatusRead(
        enabled=current_user.scheduler_enabled
        and (settings.scheduler_enabled or any(schedule.enabled for schedule in schedules)),
        running=bool(
            scheduler_instance
            and scheduler_instance.running
            and current_user.scheduler_enabled
            and (
                settings.scheduler_enabled
                or any(schedule.enabled for schedule in schedules)
            )
        ),
        interval_minutes=settings.scheduler_interval_minutes,
        next_run_time=job.next_run_time if job else None,
        last_tick=scheduler_service.get_last_tick_status(),
        schedules=[
            _schedule_read(
                schedule,
                scheduler_instance,
                user_enabled=current_user.scheduler_enabled,
            )
            for schedule in schedules
        ],
        recent_cycles=recent_cycles,
        trade_execution_note=(
            "REQUIRED proposals wait for POST /agent/approvals/{approval_id}/approve; "
            "OBSERVE_ONLY proposals never submit orders; AUTO proposals are re-checked "
            "against live risk policies before paper-order submission."
        ),
    )


@router.get(
    "/scheduler/logs",
    response_model=SchedulerLogsRead,
    summary="View recent scheduler interval results",
)
async def get_scheduler_logs(
    schedule_id: uuid.UUID | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SchedulerLogsRead:
    """Return recent tick summaries, including intervals skipped before a cycle."""
    if not 1 <= limit <= 100:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 100")

    schedule_ids: Sequence[uuid.UUID]
    if schedule_id is not None:
        schedule_result = await db.execute(
            select(AgentSchedule.id).where(
                AgentSchedule.id == schedule_id,
                AgentSchedule.user_id == current_user.id,
            )
        )
        if schedule_result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Schedule not found")
        schedule_ids = [schedule_id]
    else:
        schedules_result = await db.execute(
            select(AgentSchedule.id).where(AgentSchedule.user_id == current_user.id)
        )
        schedule_ids = schedules_result.scalars().all()

    logs = [
        SchedulerTickLog(schedule_id=None, tick=tick)
        for tick in scheduler_service.get_tick_history(limit=limit)
    ]
    for owned_schedule_id in schedule_ids:
        logs.extend(
            SchedulerTickLog(schedule_id=owned_schedule_id, tick=tick)
            for tick in scheduler_service.get_tick_history(
                owned_schedule_id, limit=limit
            )
        )
    logs.sort(key=lambda log: log.tick.started_at, reverse=True)
    return SchedulerLogsRead(logs=logs[:limit])
