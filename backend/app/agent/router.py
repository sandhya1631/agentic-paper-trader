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

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agent import scheduler as scheduler_service
from app.agent.approval import evaluate_and_submit_cycle_trade
from app.agent.orchestrator import CycleOverlapError, run_agent_cycle
from app.agent.watchlist import normalize_watchlist
from app.audit import record_audit_event
from app.auth.dependencies import get_current_user, require_operator
from app.broker.gateway import TradingDisabledError
from app.core.config import get_settings
from app.core.constants import ALPACA_PROVIDER, DEFAULT_STRATEGY_VERSION, DEFAULT_WATCHLIST
from app.db.enums import (
    ApprovalStatus,
    BrokerOrderStatus,
    CycleStatus,
    CycleTrigger,
    DecisionAction,
    PolicyDecisionType,
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
    get_latest_activation_reason,
    list_kill_switch_events,
    set_kill_switch,
    set_user_scheduler_switch,
)
from app.schemas import (
    AgentConfigDetailRead,
    AgentConfigUpdate,
    AgentScheduleRead,
    ApprovalAction,
    ApprovalRead,
    CycleListRead,
    CycleRead,
    KillSwitchEventRead,
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
    enabled = settings.kill_switch_enabled or bool(control and control.kill_switch_enabled)
    return KillSwitchRead(
        enabled=enabled,
        enforced_by_environment=settings.kill_switch_enabled,
        updated_at=control.updated_at if control else None,
        updated_by=control.updated_by if control else None,
        reason=await get_latest_activation_reason(db) if enabled else None,
    )


@router.get(
    "/kill-switch/events",
    response_model=list[KillSwitchEventRead],
    summary="Audit trail of kill-switch changes, newest first (admin only)",
)
async def list_kill_switch_audit(
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[KillSwitchEventRead]:
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin role required")
    events = await list_kill_switch_events(db, limit)

    # Denormalize emails for the UI (#169) in one extra query, rather than making
    # it resolve actor_id/target_user_id itself. None for a user who no longer
    # exists (both columns are ON DELETE SET NULL).
    user_ids = {e.actor_id for e in events if e.actor_id} | {
        e.target_user_id for e in events if e.target_user_id
    }
    emails_by_id: dict[uuid.UUID, str] = {}
    if user_ids:
        rows = await db.execute(select(User.id, User.email).where(User.id.in_(user_ids)))
        emails_by_id = {row.id: row.email for row in rows}

    return [
        KillSwitchEventRead(
            id=event.id,
            occurred_at=event.occurred_at,
            actor_id=event.actor_id,
            previous_enabled=event.previous_enabled,
            new_enabled=event.new_enabled,
            outcome=event.outcome,
            reason=event.reason,
            scope=event.scope,
            target_user_id=event.target_user_id,
            actor_email=emails_by_id.get(event.actor_id) if event.actor_id else None,
            target_user_email=(
                emails_by_id.get(event.target_user_id) if event.target_user_id else None
            ),
        )
        for event in events
    ]


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
            reason=body.reason,
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
        reason=body.reason if body.enabled else None,
    )


_NO_AGENT_DETAIL = "No agent configured yet. Trigger a cycle to create your default agent."


def _agent_config_not_found(agent_id: uuid.UUID | None) -> HTTPException:
    """404 for GET/PATCH /agent/config. A named agent_id that the caller doesn't own (or
    that doesn't exist) is "Agent not found", which reveals nothing about other users'
    agents; without an agent_id the caller simply has no agent yet."""
    detail = "Agent not found" if agent_id is not None else _NO_AGENT_DETAIL
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


async def _get_owned_agent(
    db: AsyncSession,
    user_id: uuid.UUID,
    agent_id: uuid.UUID | None = None,
    *,
    for_update: bool = False,
) -> AgentConfig | None:
    """Return the caller's agent (a specific one when agent_id is given), else None.

    Ownership runs AgentConfig -> TradingAccount.user_id. The deterministic order means
    the agent GET/PATCH /agent/config edits is the same agent POST /agent/cycles/run runs.
    """
    stmt = (
        select(AgentConfig)
        .join(TradingAccount, AgentConfig.account_id == TradingAccount.id)
        .where(TradingAccount.user_id == user_id)
    )
    if agent_id is not None:
        stmt = stmt.where(AgentConfig.id == agent_id)
    stmt = stmt.order_by(AgentConfig.created_at, AgentConfig.id).limit(1)
    if for_update:
        stmt = stmt.with_for_update(of=AgentConfig)
    return (await db.execute(stmt)).scalar_one_or_none()


def _agent_config_detail(agent: AgentConfig) -> AgentConfigDetailRead:
    """The GET/PATCH /agent/config response, with the watchlist normalized (#179).

    A stored list may predate the PATCH validation (e.g. `["aapl", "AAPL"]` from an
    earlier SQL edit). Returning the canonical list the cycles actually use (trimmed,
    upper-case, de-duplicated) keeps the Settings card from loading with unsaved
    changes; the stored row itself is left as it is.
    """
    detail = AgentConfigDetailRead.model_validate(agent)
    return detail.model_copy(update={"watchlist": normalize_watchlist(agent.watchlist)})


@router.get(
    "/config",
    response_model=AgentConfigDetailRead,
    summary="Get the caller's agent configuration",
)
async def get_agent_config(
    agent_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AgentConfigDetailRead:
    """Return the caller's agent config. Never provisions an agent (404 when none)."""
    agent = await _get_owned_agent(db, current_user.id, agent_id)
    if agent is None:
        raise _agent_config_not_found(agent_id)
    return _agent_config_detail(agent)


@router.patch(
    "/config",
    response_model=AgentConfigDetailRead,
    summary="Update the caller's agent watchlist / approval mode (operator)",
)
async def update_agent_config(
    body: AgentConfigUpdate,
    agent_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_operator),
) -> AgentConfigDetailRead:
    """Apply the changed fields and audit them in the same commit.

    Only the watchlist and approval mode are editable (the body forbids every other
    field). OPERATOR/ADMIN only: switching to AUTO removes human review, which a VIEWER
    (who cannot approve trades) must not be able to do. The next cycle reads the new
    values; risk policies still gate every order.
    """
    agent = await _get_owned_agent(db, current_user.id, agent_id, for_update=True)
    if agent is None:
        raise _agent_config_not_found(agent_id)

    changes: dict[str, dict[str, Any]] = {}
    current_watchlist = list(agent.watchlist or [])
    if body.watchlist is not None and body.watchlist != current_watchlist:
        changes["watchlist"] = {"from": current_watchlist, "to": list(body.watchlist)}
        # Assign a NEW list: the JSON column is not mutation-tracked.
        agent.watchlist = list(body.watchlist)
    if body.approval_mode is not None and body.approval_mode != agent.approval_mode:
        changes["approval_mode"] = {
            "from": agent.approval_mode.value,
            "to": body.approval_mode.value,
        }
        agent.approval_mode = body.approval_mode

    if changes:
        agent.version += 1
        record_audit_event(
            db,
            event_type="AGENT_CONFIG_UPDATED",
            entity_type="agent_config",
            entity_id=agent.id,
            actor_id=current_user.id,
            details={"changes": changes},
        )
        await db.commit()
        await db.refresh(agent)
    return _agent_config_detail(agent)


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
        agent = await _get_owned_agent(db, current_user.id)
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
        agent = await _get_owned_agent(db, current_user.id, agent_id)
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


@router.get(
    "/cycles",
    response_model=CycleListRead,
    summary="List agent cycles for the current user (paginated, newest first)",
)
async def list_agent_cycles(
    limit: int = 20,
    offset: int = 0,
    started_after: datetime | None = None,
    started_before: datetime | None = None,
    action: DecisionAction | None = None,
    status_filter: CycleStatus | None = Query(default=None, alias="status"),
    policy_decision: PolicyDecisionType | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CycleListRead:
    """Return the authenticated user's agent cycles, newest first, paginated.

    Shared read surface for the dashboard cycle-history (#79), decision timeline
    (#81), and audit history (#82). Covers both MANUAL and SCHEDULED cycles and is
    ownership-scoped: a user only ever sees cycles belonging to their own agents.

    Every filter is optional and they AND together. ``action`` matches the LLM's
    proposed action (``proposal_json.action``); ``policy_decision`` matches the
    cycle's overall, first-class outcome (ALLOW/DENY/RESIZE/NOT_EVALUATED) rather
    than any single policy's result, which would need a slower, unindexed JSON
    search over ``policy_result_json.evaluations`` (#82's noted follow-up).
    """
    if not 1 <= limit <= 100:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 100")
    if offset < 0:
        raise HTTPException(status_code=422, detail="offset must be >= 0")
    if started_after is not None and started_before is not None and started_after > started_before:
        raise HTTPException(
            status_code=422, detail="started_after must not be after started_before"
        )

    owned_cycles = (
        select(AgentCycle)
        .join(AgentConfig, AgentCycle.agent_id == AgentConfig.id)
        .join(TradingAccount, AgentConfig.account_id == TradingAccount.id)
        .where(TradingAccount.user_id == current_user.id)
    )
    if started_after is not None:
        owned_cycles = owned_cycles.where(AgentCycle.started_at >= started_after)
    if started_before is not None:
        owned_cycles = owned_cycles.where(AgentCycle.started_at <= started_before)
    if action is not None:
        # proposal_json is a plain JSON column (not JSONB), so comparing it to a
        # string needs the explicit as_string() cast - Postgres has no json = text
        # operator (confirmed: raises UndefinedFunctionError without this).
        owned_cycles = owned_cycles.where(
            AgentCycle.proposal_json["action"].as_string() == action.value
        )
    if status_filter is not None:
        owned_cycles = owned_cycles.where(AgentCycle.status == status_filter)
    if policy_decision is not None:
        owned_cycles = owned_cycles.where(AgentCycle.policy_decision == policy_decision.value)

    total = await db.scalar(
        select(func.count()).select_from(owned_cycles.subquery())
    ) or 0

    result = await db.execute(
        owned_cycles.options(selectinload(AgentCycle.approval))
        # Secondary sort by id keeps pagination deterministic when two cycles
        # share a started_at, so pages never overlap or skip a row.
        .order_by(AgentCycle.started_at.desc(), AgentCycle.id.desc())
        .limit(limit)
        .offset(offset)
    )
    cycles = result.scalars().all()

    next_offset = offset + limit if offset + limit < total else None
    return CycleListRead(
        items=[CycleRead.model_validate(cycle) for cycle in cycles],
        total=total,
        limit=limit,
        offset=offset,
        next_offset=next_offset,
    )


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
    current_user: User = Depends(require_operator),
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
    # Record the fresh binding decision as the cycle's queryable policy result (#121),
    # overwriting the earlier pending-approval preview. The signed token is excluded.
    cycle.policy_decision = policy_decision.decision
    cycle.policy_result_json = policy_decision.model_dump(mode="json", exclude={"policy_token"})
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
    current_user: User = Depends(require_operator),
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
    await set_user_scheduler_switch(db, user=current_user, scheduler_enabled=True)
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
        await set_user_scheduler_switch(db, user=current_user, scheduler_enabled=True)
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
    await set_user_scheduler_switch(db, user=current_user, scheduler_enabled=False)
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