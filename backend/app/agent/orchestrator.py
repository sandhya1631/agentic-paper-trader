"""Agent Cycle Orchestrator Engine.

Orchestrates automated and manual agent cycles:
  1. Rejects the request if this agent already has a non-terminal cycle in
     progress (`CycleOverlapError`) — active cycles never overlap.
  2. Creates an `AgentCycle` record in PostgreSQL (`PENDING`).
  3. Collects OHLCV market bars for the watchlist and calculates indicators.
  4. Builds sanitized context via `build_agent_context`.
  5. Retrieves the versioned system prompt (`S-001`).
  6. Queries the configured LLM client (Ollama / OpenAI via `get_llm_client`).
  7. Validates the decision output via `validate_decision_proposal`.
  8. On JSON/schema validation error, attempts 1 self-repair prompt retry.
  9. If self-repair also fails, the cycle ends as FAILED (LLM_OUTPUT_INVALID); nothing is guessed.
  10. Persists complete cycle state (`snapshot_json`, prompt version/revision/text,
      `llm_raw_response`, `proposal_json`) to PostgreSQL and marks status `SUCCESS` or `FAILED`.
"""

import hashlib
import json
import logging
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.agent.approval import (
    evaluate_and_submit_cycle_trade,
    expire_pending_approvals,
    reconcile_uncertain_cycle,
)
from app.agent.context import build_agent_context
from app.agent.prompt import get_prompt_definition
from app.agent.tool_validation import ToolValidationError, validate_decision_proposal
from app.broker.gateway import TradingDisabledError
from app.core.config import get_settings
from app.core.constants import ACTIVE_CYCLE_STATUSES, POLICY_VERSION
from app.db.enums import (
    ApprovalMode,
    ApprovalStatus,
    CycleStatus,
    CycleTrigger,
    DecisionAction,
)
from app.db.models import AgentConfig, AgentCycle, CycleApproval, utcnow
from app.llm.base import LLMClient
from app.llm.factory import get_llm_client
from app.market_data.alpaca_client import MarketDataCollector, create_market_data_collector
from app.schemas import DecisionProposal

logger = logging.getLogger(__name__)


class CycleOverlapError(Exception):
    """Raised when the agent already has a non-terminal cycle in progress."""


async def run_agent_cycle(
    agent_id: uuid.UUID,
    db: AsyncSession,
    *,
    trigger: CycleTrigger = CycleTrigger.MANUAL,
    llm_client: LLMClient | None = None,
    collector: MarketDataCollector | None = None,
) -> AgentCycle:
    """Execute a single end-to-end agent decision cycle."""
    # 1. Fetch AgentConfig
    stmt = select(AgentConfig).where(AgentConfig.id == agent_id)
    res = await db.execute(stmt)
    agent = res.scalar_one_or_none()
    if not agent:
        raise ValueError(f"AgentConfig with id '{agent_id}' not found")

    # 2. Gate: reject if this agent already has an active cycle (no overlap)
    # Expire stale approvals first so a forgotten PENDING_APPROVAL cycle
    # doesn't block a fresh attempt forever - applies to every caller
    # (manual trigger, scheduler tick), not just the scheduler's own tick.
    await expire_pending_approvals(db, agent.id)
    active_stmt = select(AgentCycle).where(
        AgentCycle.agent_id == agent.id,
        AgentCycle.status.in_(ACTIVE_CYCLE_STATUSES),
    )
    active_res = await db.execute(active_stmt)
    active_cycle = active_res.scalars().first()
    if active_cycle is not None and active_cycle.status == CycleStatus.RECONCILING:
        try:
            resolved = await reconcile_uncertain_cycle(db, agent, active_cycle)
        except Exception:
            logger.exception(
                "Could not reconcile uncertain cycle %s; keeping it active",
                active_cycle.id,
            )
            resolved = False
        if resolved:
            active_cycle = None
    if active_cycle is not None:
        raise CycleOverlapError(
            f"Agent '{agent_id}' already has an active cycle "
            f"'{active_cycle.id}' (status={active_cycle.status})"
        )

    # 3. Create AgentCycle record (PENDING)
    prompt_definition = get_prompt_definition(agent.strategy_version)
    cycle = AgentCycle(
        agent_id=agent.id,
        trigger=trigger,
        status=CycleStatus.PENDING,
        started_at=utcnow(),
        correlation_id=uuid.uuid4(),
        prompt_version=agent.strategy_version,
        prompt_revision=prompt_definition.revision,
    )
    db.add(cycle)
    await db.commit()
    await db.refresh(cycle)

    try:
        cycle.status = CycleStatus.COLLECTING
        await db.commit()

        # 4. Collect Market Data
        if collector is None:
            try:
                collector = create_market_data_collector()
            except ValueError:
                collector = None

        bars_per_symbol: dict[str, list[Any]] = {}
        if collector:
            try:
                bars_per_symbol = await collector.fetch_latest_bars(
                    agent.watchlist, limit=50, lookback_days=30, validate=False
                )
            except Exception as exc:
                logger.warning("Failed to fetch market bars for cycle: %s", exc)

        # 5. Build Context & Get Versioned Prompt
        context = build_agent_context(
            symbols=agent.watchlist,
            bars_per_symbol=bars_per_symbol,
            strategy_version=agent.strategy_version,
        )
        context["versions"] = {
            "strategy": agent.strategy_version,
            "policy": POLICY_VERSION,
        }

        system_prompt = prompt_definition.text
        prompt_input = json.dumps(context, indent=2)

        # 6. Execute LLM Call
        cycle.status = CycleStatus.REASONING
        await db.commit()
        if llm_client is None:
            llm_client = get_llm_client()

        raw_response = await llm_client.generate(prompt_input, system=system_prompt)

        # 7. Validate Output with 1-Retry Self-Repair
        cycle.status = CycleStatus.VALIDATING
        await db.commit()
        proposal: DecisionProposal
        try:
            proposal = validate_decision_proposal(raw_response)
        except ToolValidationError as first_err:
            logger.warning(
                "Initial LLM response failed schema validation: %s. Attempting self-repair retry.",
                first_err,
            )
            repair_prompt = (
                f"Your previous response failed JSON validation: {first_err}.\n"
                f"Please return ONLY a single valid JSON object matching the required schema.\n"
                f"Previous Context:\n{prompt_input}"
            )
            first_response = raw_response
            try:
                repaired_response = await llm_client.generate(repair_prompt, system=system_prompt)
                raw_response = repaired_response
                proposal = validate_decision_proposal(repaired_response)
            except ToolValidationError as repair_err:
                # Exactly one repair attempt is allowed. If it also fails, the cycle ENDS:
                # no proposal, no guessed symbol or quantity, and the broker is never reached.
                logger.error(
                    "Self-repair retry also failed schema validation: %s. Ending cycle.",
                    repair_err,
                )
                cycle.status = CycleStatus.FAILED
                cycle.failure_code = "LLM_OUTPUT_INVALID"
                cycle.failure_message = str(repair_err)
                cycle.snapshot_json = context
                cycle.prompt_version = agent.strategy_version
                cycle.prompt_revision = prompt_definition.revision
                cycle.prompt_text = system_prompt
                cycle.llm_raw_response = (
                    f"FIRST RESPONSE:\n{first_response}\n\nREPAIR RESPONSE:\n{repaired_response}"
                )
                cycle.completed_at = utcnow()
                await db.commit()
                await db.refresh(cycle)
                set_committed_value(cycle, "approval", None)
                return cycle

        # 8. Persist Cycle Success & Snapshot State
        cycle.snapshot_json = context
        cycle.prompt_version = agent.strategy_version
        cycle.prompt_revision = prompt_definition.revision
        cycle.prompt_text = system_prompt
        cycle.llm_raw_response = raw_response
        cycle.proposal_json = proposal.model_dump(mode="json")
        pending_approval = None

        if proposal.action == DecisionAction.HOLD:
            cycle.status = CycleStatus.HOLD
            cycle.completed_at = utcnow()
            context["execution_status"] = "NO_TRADE_PROPOSED"
        elif agent.approval_mode == ApprovalMode.OBSERVE_ONLY:
            cycle.status = CycleStatus.COMPLETED
            cycle.completed_at = utcnow()
            context["execution_status"] = "OBSERVE_ONLY"
        elif agent.approval_mode == ApprovalMode.REQUIRED:
            proposal_hash = hashlib.sha256(
                json.dumps(cycle.proposal_json, sort_keys=True).encode()
            ).hexdigest()
            # Dry-run the policies so the operator can SEE the evidence next to the
            # proposal (Issue #76). Nothing is submitted, the signed policy token is not
            # stored, and the binding re-check still runs again on approval (Issue #77).
            policy_preview = None
            try:
                # Read-only: no autoflush, so the half-built cycle is not saved early.
                with db.no_autoflush:
                    policy_preview, _ = await evaluate_and_submit_cycle_trade(
                        db, agent, cycle, submit=False
                    )
            except Exception:
                logger.warning(
                    "Could not preview policies for cycle %s; parking without them",
                    cycle.id,
                    exc_info=True,
                )
            approval = CycleApproval(
                cycle_id=cycle.id,
                status=ApprovalStatus.PENDING,
                proposal_hash=proposal_hash,
                expires_at=utcnow() + timedelta(minutes=get_settings().approval_expiry_minutes),
                policy_version=policy_preview.policy_version if policy_preview else None,
                policy_result_json=(
                    policy_preview.model_dump(mode="json", exclude={"policy_token"})
                    if policy_preview
                    else None
                ),
            )
            db.add(approval)
            pending_approval = approval
            cycle.approval = approval
            cycle.status = CycleStatus.PENDING_APPROVAL
            context["execution_status"] = "PENDING_APPROVAL"
        else:
            cycle.status = CycleStatus.EXECUTING
            context["execution_status"] = "EXECUTING"
            await db.commit()
            try:
                policy_decision, broker_order = await evaluate_and_submit_cycle_trade(
                    db,
                    agent,
                    cycle,
                )
            except TradingDisabledError as exc:
                cycle.status = CycleStatus.REJECTED
                cycle.failure_code = "KILL_SWITCH_ENABLED"
                cycle.failure_message = str(exc)
                cycle.completed_at = utcnow()
                context["execution_status"] = "KILL_SWITCH_ENABLED"
                cycle.snapshot_json = context
                await db.commit()
                await db.refresh(cycle)
                set_committed_value(cycle, "approval", None)
                return cycle
            except Exception as exc:
                logger.exception("Automatic paper-order execution needs reconciliation")
                cycle.status = CycleStatus.RECONCILING
                cycle.failure_code = "ORDER_EXECUTION_UNCERTAIN"
                cycle.failure_message = str(exc)
                cycle.completed_at = utcnow()
                context["execution_status"] = "RECONCILIATION_REQUIRED"
                cycle.snapshot_json = context
                await db.commit()
                await db.refresh(cycle)
                set_committed_value(cycle, "approval", None)
                return cycle
            context["policy_evaluation"] = policy_decision.model_dump(mode="json")
            if broker_order is None:
                cycle.status = CycleStatus.REJECTED
                cycle.failure_code = "POLICY_DENIED"
                context["execution_status"] = "POLICY_DENIED"
            elif broker_order.status.value == "REJECTED":
                cycle.status = CycleStatus.REJECTED
                cycle.failure_code = "BROKER_REJECTED"
                cycle.failure_message = broker_order.rejection_reason
                context["execution_status"] = "ORDER_REJECTED"
            else:
                cycle.status = CycleStatus.COMPLETED
                context["execution_status"] = "ORDER_SUBMITTED"
            cycle.completed_at = utcnow()

        cycle.snapshot_json = context
        await db.commit()
        await db.refresh(cycle)
        if pending_approval is not None:
            await db.refresh(pending_approval)
        set_committed_value(cycle, "approval", pending_approval)
        return cycle

    except Exception as exc:
        logger.error("Agent cycle failed unexpectedly: %s", exc, exc_info=True)
        cycle.status = CycleStatus.FAILED
        cycle.completed_at = utcnow()
        cycle.failure_code = "UNEXPECTED_ERROR"
        cycle.failure_message = str(exc)
        await db.commit()
        await db.refresh(cycle)
        raise
