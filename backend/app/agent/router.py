"""Agent Router Endpoints (Story 2.3.2 / Issue #28).

Exposes HTTP endpoints for triggering manual agent cycles and querying cycle details.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.orchestrator import run_agent_cycle
from app.auth.dependencies import get_current_user
from app.db.enums import CycleTrigger
from app.db.models import AgentConfig, AgentCycle, PolicySet, TradingAccount, User
from app.db.session import get_db
from app.schemas import CycleRead

router = APIRouter(prefix="/agent", tags=["agent"])


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
        stmt = select(AgentConfig).limit(1)
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
                    provider="ALPACA",
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
                watchlist=["AAPL", "NVDA", "SPY"],
                strategy_version="S-001",
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
        target_agent_id = agent_id

    try:
        cycle = await run_agent_cycle(
            target_agent_id, db, trigger=CycleTrigger.MANUAL
        )
        return cycle
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
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
    _current_user: User = Depends(get_current_user),
) -> Any:
    """Retrieve details and proposal status of a specific agent cycle."""
    stmt = select(AgentCycle).where(AgentCycle.id == cycle_id)
    res = await db.execute(stmt)
    cycle = res.scalar_one_or_none()
    if not cycle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"AgentCycle '{cycle_id}' not found.",
        )
    return cycle
