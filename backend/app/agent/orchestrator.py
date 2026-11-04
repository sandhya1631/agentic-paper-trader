"""Agent Cycle Orchestrator Engine.

Orchestrates automated and manual agent cycles:
  1. Takes the agent's cycle lock (`agent_cycle_lock`, a Postgres advisory lock), so a
     manual run and a scheduled watchlist sweep (`run_watchlist_cycles`) for the same
     agent never run concurrently; then rejects the request if this agent already has
     a non-terminal cycle in progress (`CycleOverlapError`). A sweep holds the lock for
     all of its symbols and checks the status gate once, on its first symbol, so its own
     per-symbol cycles can coexist (e.g. several PENDING_APPROVAL in REQUIRED mode).
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

import contextlib
import hashlib
import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

from sqlalchemy import BigInteger, bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
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
    PolicyDecisionType,
)
from app.db.models import AgentConfig, AgentCycle, CycleApproval, utcnow
from app.llm.base import LLMClient
from app.llm.factory import get_llm_client
from app.market_data.alpaca_client import MarketDataCollector, create_market_data_collector
from app.schemas import DecisionProposal

logger = logging.getLogger(__name__)


class CycleOverlapError(Exception):
    """Raised when the agent already has a non-terminal cycle in progress."""


_AGENT_LOCK_SQL = text("SELECT pg_try_advisory_xact_lock(:key)").bindparams(
    bindparam("key", type_=BigInteger)
)


def _agent_lock_key(agent_id: uuid.UUID) -> int:
    """A signed 64-bit advisory-lock key for the agent, identical in every process."""
    digest = hashlib.blake2b(b"agent-cycle:" + agent_id.bytes, digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


@contextlib.asynccontextmanager
async def agent_cycle_lock(db: AsyncSession, agent_id: uuid.UUID) -> AsyncIterator[None]:
    """Hold this agent's cycle lock for the block, or raise CycleOverlapError at once.

    Serializes a manual run (API process) and a scheduled sweep (Celery worker) for
    the same agent, so two concurrent cycles can never both submit an order. The
    Postgres advisory lock lives in its own transaction on a dedicated connection that
    stays open for the whole block: a lock taken on ``db`` itself would be released
    by the cycle's first commit. Every exit path (return, exception, cancellation,
    connection loss) ends that transaction, so the lock cannot leak.
    """
    engine = db.bind
    if not isinstance(engine, AsyncEngine):
        raise RuntimeError("agent_cycle_lock requires a session bound to an AsyncEngine")
    async with engine.connect() as conn, conn.begin():
        if not await conn.scalar(_AGENT_LOCK_SQL, {"key": _agent_lock_key(agent_id)}):
            raise CycleOverlapError(f"Agent '{agent_id}' already has a cycle in progress")
        yield


async def run_agent_cycle(
    agent_id: uuid.UUID,
    db: AsyncSession,
    *,
    trigger: CycleTrigger = CycleTrigger.MANUAL,
    llm_client: LLMClient | None = None,
    collector: MarketDataCollector | None = None,
    target_symbol: str | None = None,
    bars_per_symbol: dict[str, list[Any]] | None = None,
    enforce_no_overlap: bool = True,
) -> AgentCycle:
    """Run one decision cycle while holding the agent's cycle lock.

    Raises CycleOverlapError at once if another cycle or sweep for this agent holds
    the lock. See ``_run_agent_cycle`` for the parameters.
    """
    async with agent_cycle_lock(db, agent_id):
        return await _run_agent_cycle(
            agent_id,
            db,
            trigger=trigger,
            llm_client=llm_client,
            collector=collector,
            target_symbol=target_symbol,
            bars_per_symbol=bars_per_symbol,
            enforce_no_overlap=enforce_no_overlap,
        )


async def _run_agent_cycle(
    agent_id: uuid.UUID,
    db: AsyncSession,
    *,
    trigger: CycleTrigger = CycleTrigger.MANUAL,
    llm_client: LLMClient | None = None,
    collector: MarketDataCollector | None = None,
    target_symbol: str | None = None,
    bars_per_symbol: dict[str, list[Any]] | None = None,
    enforce_no_overlap: bool = True,
) -> AgentCycle:
    """Execute a single end-to-end agent decision cycle (caller holds the agent lock).

    When ``target_symbol`` is given, the decision is scoped to that symbol: the
    prompt is told to decide for it, and a proposal naming any other symbol is
    coerced to HOLD (never act on a symbol this cycle did not evaluate). When
    ``bars_per_symbol`` is supplied those bars are used instead of fetching, so a
    watchlist sweep fetches once. ``enforce_no_overlap`` runs the active-cycle
    gate; a sweep runs it once (first symbol) and skips it for the rest, so one
    tick can produce a cycle per symbol without each blocking the next.
    """
    # 1. Fetch AgentConfig
    stmt = select(AgentConfig).where(AgentConfig.id == agent_id)
    res = await db.execute(stmt)
    agent = res.scalar_one_or_none()
    if not agent:
        raise ValueError(f"AgentConfig with id '{agent_id}' not found")

    # 2. Gate: reject if this agent already has an active cycle (no overlap).
    # A watchlist sweep runs this gate once (first symbol) and skips it for the
    # rest (enforce_no_overlap=False), so sequential per-symbol cycles in one
    # tick don't block each other.
    if enforce_no_overlap:
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

        # 4. Collect Market Data (reuse caller-provided bars for a watchlist sweep)
        if bars_per_symbol is None:
            if collector is None:
                try:
                    collector = create_market_data_collector()
                except ValueError:
                    collector = None

            bars_per_symbol = {}
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
        # Scope this cycle to one symbol (watchlist sweep); the prompt decides for it.
        if target_symbol is not None:
            context["decision_target"] = target_symbol

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
        # Scope guard: never act on a symbol this cycle did not target. If the
        # model answered for a different ticker, record a HOLD for the target.
        if target_symbol is not None and proposal.symbol != target_symbol:
            logger.warning(
                "Cycle %s targeted %s but the model proposed %s; coercing to HOLD.",
                cycle.id,
                target_symbol,
                proposal.symbol,
            )
            proposal = DecisionProposal(
                symbol=target_symbol,
                action=DecisionAction.HOLD,
                quantity=0,
                reasoning=(
                    f"Model proposed {proposal.symbol}, but this cycle evaluates "
                    f"{target_symbol}; holding. Original reasoning: {proposal.reasoning}"
                ),
            )
        cycle.proposal_json = proposal.model_dump(mode="json")
        pending_approval = None

        if proposal.action == DecisionAction.HOLD:
            cycle.status = CycleStatus.HOLD
            cycle.completed_at = utcnow()
            # No order is ever proposed, so policies are intentionally not run (#121).
            cycle.policy_decision = PolicyDecisionType.NOT_EVALUATED
            context["execution_status"] = "NO_TRADE_PROPOSED"
        elif agent.approval_mode == ApprovalMode.OBSERVE_ONLY:
            cycle.status = CycleStatus.COMPLETED
            cycle.completed_at = utcnow()
            cycle.policy_decision = PolicyDecisionType.NOT_EVALUATED
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
            # Persist the preview as the cycle's queryable policy record (#121). This is
            # the same read-only dry run shown on the approval; the binding re-check still
            # runs again at approval time, so this never weakens the execution gate.
            if policy_preview is not None:
                cycle.policy_decision = policy_preview.decision
                cycle.policy_result_json = policy_preview.model_dump(
                    mode="json", exclude={"policy_token"}
                )
            else:
                cycle.policy_decision = PolicyDecisionType.NOT_EVALUATED
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
                cycle.policy_decision = PolicyDecisionType.NOT_EVALUATED
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
                cycle.policy_decision = PolicyDecisionType.NOT_EVALUATED
                context["execution_status"] = "RECONCILIATION_REQUIRED"
                cycle.snapshot_json = context
                await db.commit()
                await db.refresh(cycle)
                set_committed_value(cycle, "approval", None)
                return cycle
            context["policy_evaluation"] = policy_decision.model_dump(mode="json")
            # Persist the same decision the execution gate just produced (#121) - this is
            # a read-only copy for audit/query, not a second evaluation.
            cycle.policy_decision = policy_decision.decision
            cycle.policy_result_json = policy_decision.model_dump(
                mode="json", exclude={"policy_token"}
            )
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


async def run_watchlist_cycles(
    agent_id: uuid.UUID,
    db: AsyncSession,
    *,
    trigger: CycleTrigger = CycleTrigger.SCHEDULED,
    llm_client: LLMClient | None = None,
    collector: MarketDataCollector | None = None,
) -> list[AgentCycle]:
    """Run a watchlist sweep while holding the agent's cycle lock for all of it.

    Raises CycleOverlapError at once if another cycle or sweep for this agent holds
    the lock. See ``_run_watchlist_cycles`` for the sweep itself.
    """
    async with agent_cycle_lock(db, agent_id):
        return await _run_watchlist_cycles(
            agent_id, db, trigger=trigger, llm_client=llm_client, collector=collector
        )


async def _run_watchlist_cycles(
    agent_id: uuid.UUID,
    db: AsyncSession,
    *,
    trigger: CycleTrigger = CycleTrigger.SCHEDULED,
    llm_client: LLMClient | None = None,
    collector: MarketDataCollector | None = None,
) -> list[AgentCycle]:
    """Run one decision cycle per watchlist symbol in a single tick (caller holds the lock).

    This is what makes the agent evaluate its whole watchlist instead of a single
    (historically AAPL-anchored) symbol. Market bars are fetched once for the
    watchlist and reused across the per-symbol cycles. The active-cycle gate runs
    once (on the first symbol); if the agent already has a cycle in progress,
    CycleOverlapError propagates and no symbols run - matching the previous
    per-agent scheduled behaviour. A failing symbol is logged (its cycle is persisted
    as FAILED) and the sweep continues; if every symbol fails, the last error is
    re-raised so the scheduler counts the agent as failed.
    """
    agent_res = await db.execute(select(AgentConfig).where(AgentConfig.id == agent_id))
    agent = agent_res.scalar_one_or_none()
    if agent is None:
        raise ValueError(f"AgentConfig with id '{agent_id}' not found")

    # Normalize to the canonical upper-case form the Ticker type produces, so the
    # per-symbol scope guard and P-005 compare against one consistent form, and drop
    # duplicates so one sweep never evaluates (or orders) the same symbol twice.
    symbols = list(dict.fromkeys(str(s).strip().upper() for s in (agent.watchlist or [])))
    if not symbols:
        return []

    # Fetch bars once for the whole watchlist; per-symbol cycles reuse them.
    bars_per_symbol: dict[str, list[Any]] = {}
    if collector is None:
        try:
            collector = create_market_data_collector()
        except ValueError:
            collector = None
    if collector:
        try:
            bars_per_symbol = await collector.fetch_latest_bars(
                symbols, limit=50, lookback_days=30, validate=False
            )
        except Exception as exc:
            logger.warning("Watchlist sweep: bar prefetch failed: %s", exc)
            bars_per_symbol = {}

    cycles: list[AgentCycle] = []
    last_error: Exception | None = None
    for index, symbol in enumerate(symbols):
        try:
            cycle = await _run_agent_cycle(
                agent_id,
                db,
                trigger=trigger,
                llm_client=llm_client,
                collector=collector,
                target_symbol=symbol,
                bars_per_symbol=bars_per_symbol,
                # Gate once on the first symbol; the rest are sequential and must not
                # block on an approval/execution the first symbol may have left active.
                enforce_no_overlap=(index == 0),
            )
            cycles.append(cycle)
        except CycleOverlapError:
            # Only the first symbol runs the gate, so this means the agent is already
            # busy: skip the whole tick, matching the previous per-agent behaviour.
            raise
        except Exception as exc:
            # One symbol's transient failure (e.g. LLM timeout) must not drop the
            # rest of the watchlist for this tick.
            logger.exception("Watchlist sweep: cycle failed for %s; continuing", symbol)
            last_error = exc
    if last_error is not None and not cycles:
        # Every symbol failed (e.g. the LLM is unreachable): surface it so the scheduler
        # reports the tick as FAILED instead of a misleading all-OK sweep.
        raise last_error
    return cycles
