"""Agent Cycle Orchestrator Engine.

Orchestrates automated and manual agent cycles:
  1. Takes the agent's cycle lock (`agent_cycle_lock`, a Postgres advisory lock), so a
     manual run and a scheduled watchlist sweep (`run_watchlist_cycles`) for the same
     agent never run concurrently; then rejects the request if this agent already has
     a non-terminal cycle in progress (`CycleOverlapError`). A sweep holds the lock for
     all of its symbols and checks the status gate once, before any market-data or
     broker read, so a busy agent's tick makes no such calls and its own per-symbol
     cycles can coexist (e.g. several PENDING_APPROVAL in REQUIRED mode).
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
    persistable_policy_result,
    reconcile_uncertain_cycle,
)
from app.agent.context import build_agent_context
from app.agent.knowledge import retrieve_knowledge
from app.agent.live_context import UNSET, LiveAccountData, Unset, fetch_live_account_data
from app.agent.prompt import get_prompt_definition
from app.agent.tool_validation import ToolValidationError, validate_decision_proposal
from app.agent.watchlist import normalize_watchlist
from app.broker.gateway import TradingDisabledError
from app.core.config import get_settings
from app.core.constants import (
    ACTIVE_CYCLE_STATUSES,
    DEFAULT_STRATEGY_VERSION,
    DYNAMIC_STRATEGY_VERSION,
    POLICY_VERSION,
)
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


def _estimate_prompt_tokens(prompt_text: str, context_json: str) -> int:
    return (len(context_json) + len(prompt_text) + 3) // 4


def _enforce_context_window(
    context: dict[str, Any],
    system_prompt: str,
    *,
    model_window_tokens: int,
) -> dict[str, Any]:
    """Trim the model payload to fit the configured context window.

    The orchestrator tracks token usage for observability, but the model input is
    still bounded by the provider's configured context window. If the payload is too
    large, we progressively reduce bar evidence while keeping the key decision
    metadata intact.
    """
    if model_window_tokens <= 0:
        return context

    def snapshot() -> tuple[int, str]:
        payload = json.dumps(context, separators=(",", ":"))
        return _estimate_prompt_tokens(system_prompt, payload), payload

    estimated_tokens, _ = snapshot()
    if estimated_tokens <= model_window_tokens:
        context["context_window_guard"] = {
            "model_window_tokens": model_window_tokens,
            "estimated_input_tokens": estimated_tokens,
            "remaining_tokens": max(model_window_tokens - estimated_tokens, 0),
            "truncated": False,
        }
        return context

    market = context.get("market", {})
    decision_target = context.get("decision_target")
    if decision_target and decision_target in market:
        context["market"] = {decision_target: market[decision_target]}
        context["watchlist"] = [decision_target]

    for symbol, symbol_context in list(market.items()):
        if decision_target and symbol != decision_target:
            continue
        if not isinstance(symbol_context, dict):
            continue
        recent_bars = symbol_context.get("recent_bars")
        if isinstance(recent_bars, list) and len(recent_bars) > 6:
            symbol_context["recent_bars"] = recent_bars[-6:]
        if isinstance(symbol_context.get("extended_indicators"), dict):
            indicators = symbol_context["extended_indicators"]
            allowed = {
                "rsi_14",
                "sma_20",
                "sma_50",
                "macd",
                "macd_signal",
                "macd_histogram",
            }
            for key in list(indicators):
                if key not in allowed:
                    indicators.pop(key, None)

    if len(context.get("watchlist", [])) > 3:
        context["watchlist"] = context["watchlist"][:3]

    # If the payload is still above the configured window, keep the target-only
    # market summary and lose the least critical non-decision metadata.
    estimated_tokens, _ = snapshot()
    if estimated_tokens > model_window_tokens:
        context.pop("knowledge", None)
        estimated_tokens, _ = snapshot()

    if estimated_tokens > model_window_tokens:
        context["market"] = {
            decision_target: context["market"].get(decision_target, {})
        } if decision_target else {}
        context["watchlist"] = [decision_target] if decision_target else []
        context["account"] = {
            "data_status": context.get("account", {}).get("data_status", "NOT_PROVIDED")
        }
        context["positions"] = []
        context["open_orders"] = []
        context["notes"] = (
            "Context truncated to fit the model window; default to HOLD on "
            "uncertainty."
        )

    estimated_tokens, payload = snapshot()
    context["context_window_guard"] = {
        "model_window_tokens": model_window_tokens,
        "estimated_input_tokens": estimated_tokens,
        "remaining_tokens": max(model_window_tokens - estimated_tokens, 0),
        "truncated": estimated_tokens > model_window_tokens,
        "payload_chars": len(payload),
    }
    return context


class CycleOverlapError(Exception):
    """Raised when the agent already has a non-terminal cycle in progress."""


def _select_dynamic_strategy(
    context: dict[str, Any],
    *,
    model_window_tokens: int,
) -> tuple[str, str]:
    """Choose a registered prompt and explain the choice for cycle audit."""
    market = context.get("market", {})
    market_values = [item for item in market.values() if isinstance(item, dict)]
    quality_values = [
        item.get("data_quality", {}).get("quality_status") for item in market_values
    ]
    if not quality_values or all(value in {"NO_DATA", "DEGRADED"} for value in quality_values):
        return (
            "S-002",
            "Selected the concise safety-focused strategy because market evidence "
            "is missing or degraded.",
        )

    regimes = {item.get("regime") for item in market_values}
    relative_volumes = [
        float(item["relative_volume_20"])
        for item in market_values
        if item.get("relative_volume_20") is not None
    ]
    if "DOWNTREND" in regimes:
        return "S-007", "Selected the capital-preservation strategy because a downtrend is present."
    if "RANGE" in regimes and all(value == "VALID" for value in quality_values):
        return (
            "S-005",
            "Selected the mean-reversion strategy because the market regime is "
            "range-bound.",
        )
    if relative_volumes and max(relative_volumes) >= 1.5:
        return (
            "S-004",
            "Selected the breakout strategy because relative volume confirms "
            "unusual activity.",
        )
    if "UPTREND" in regimes:
        return "S-003", "Selected the trend-following strategy because an uptrend is present."

    estimated_s001_tokens = _estimate_prompt_tokens(
        get_prompt_definition("S-006").text,
        json.dumps(context, separators=(",", ":")),
    )
    if estimated_s001_tokens > int(model_window_tokens * 0.7):
        return (
            "S-002",
            "Selected the concise strategy because the full technical prompt "
            "would consume more than 70% of the model context window.",
        )
    return (
        "S-006",
        "Selected the momentum-continuation strategy because multiple signals "
        "are available.",
    )


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
    by the cycle's first commit. Every exit path ends that transaction, so the lock
    cannot leak. A release error (e.g. the connection dropped mid-run, which already
    released the lock server-side) is logged, not raised, so a cycle that finished is
    never reported as failed.
    """
    engine = db.bind
    if not isinstance(engine, AsyncEngine):
        raise RuntimeError("agent_cycle_lock requires a session bound to an AsyncEngine")
    async with engine.connect() as conn:
        lock_txn = await conn.begin()
        try:
            if not await conn.scalar(_AGENT_LOCK_SQL, {"key": _agent_lock_key(agent_id)}):
                raise CycleOverlapError(f"Agent '{agent_id}' already has a cycle in progress")
            yield
        finally:
            try:
                await lock_txn.rollback()  # ends the lock's transaction, releasing the lock
            except Exception:
                logger.warning(
                    "Agent %s: cycle-lock connection was lost before release",
                    agent_id,
                    exc_info=True,
                )


async def _enforce_no_active_cycle(db: AsyncSession, agent: AgentConfig) -> None:
    """The active-cycle gate: raise CycleOverlapError if the agent is already busy.

    Expires stale approvals first, so a forgotten PENDING_APPROVAL cycle doesn't block
    a fresh attempt forever, then looks for a non-terminal cycle. A RECONCILING cycle
    is reconciled against the broker once; if that resolves it, the agent is free.
    Shared by both entry points: a manual cycle runs it inside ``_run_agent_cycle``,
    and a watchlist sweep runs it once before its bar prefetch and live-account read,
    so a busy agent's tick makes no market-data or account calls (#178).
    """
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
            f"Agent '{agent.id}' already has an active cycle "
            f"'{active_cycle.id}' (status={active_cycle.status})"
        )


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
    account_data: LiveAccountData | None | Unset = UNSET,
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
            account_data=account_data,
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
    account_data: LiveAccountData | None | Unset = UNSET,
) -> AgentCycle:
    """Execute a single end-to-end agent decision cycle (caller holds the agent lock).

    When ``target_symbol`` is given, the decision is scoped to that symbol: the
    prompt is told to decide for it, and a proposal naming any other symbol is
    coerced to HOLD (never act on a symbol this cycle did not evaluate). When
    ``bars_per_symbol`` is supplied those bars are used instead of fetching, so a
    watchlist sweep fetches once. ``enforce_no_overlap`` runs the active-cycle
    gate; a sweep runs that gate itself, once, before its broker reads, and skips it
    for every symbol, so one tick can produce a cycle per symbol without each
    blocking the next.
    ``account_data`` is the live paper account shown to the model; when omitted it is
    fetched here, and a sweep passes it in so the broker is read once per tick.
    ``None`` means "could not be read" and the context says UNAVAILABLE (never defaults).
    """
    # 1. Fetch AgentConfig. populate_existing re-reads the committed row even when the
    # session already holds this agent (a sweep reuses one session), so a config change
    # (PATCH /agent/config) applies to every later cycle, including the rest of a sweep.
    stmt = (
        select(AgentConfig)
        .where(AgentConfig.id == agent_id)
        .execution_options(populate_existing=True)
    )
    res = await db.execute(stmt)
    agent = res.scalar_one_or_none()
    if not agent:
        raise ValueError(f"AgentConfig with id '{agent_id}' not found")

    # 2. Gate: reject if this agent already has an active cycle (no overlap).
    # A watchlist sweep runs this gate itself, once, before it reads the broker, and
    # passes enforce_no_overlap=False for every symbol, so sequential per-symbol
    # cycles in one tick don't block each other.
    if enforce_no_overlap:
        await _enforce_no_active_cycle(db, agent)

    # 3. Create AgentCycle record (PENDING)
    initial_strategy = (
        DEFAULT_STRATEGY_VERSION
        if agent.strategy_version == DYNAMIC_STRATEGY_VERSION
        else agent.strategy_version
    )
    prompt_definition = get_prompt_definition(initial_strategy)
    cycle = AgentCycle(
        agent_id=agent.id,
        trigger=trigger,
        status=CycleStatus.PENDING,
        started_at=utcnow(),
        correlation_id=uuid.uuid4(),
        prompt_version=initial_strategy,
        prompt_revision=prompt_definition.revision,
    )
    db.add(cycle)
    await db.commit()
    await db.refresh(cycle)

    try:
        cycle.status = CycleStatus.COLLECTING
        await db.commit()

        # 4. Collect Market Data (reuse caller-provided bars for a watchlist sweep)
        symbols = normalize_watchlist(agent.watchlist)
        if bars_per_symbol is None:
            if collector is None:
                try:
                    collector = create_market_data_collector()
                except ValueError:
                    collector = None

            bars_per_symbol = {}
            if collector:
                try:
                    settings = get_settings()
                    bars_per_symbol = await collector.fetch_latest_bars(
                        symbols,
                        limit=settings.default_bar_limit,
                        lookback_days=30,
                        validate=False,
                    )
                except Exception as exc:
                    logger.warning("Failed to fetch market bars for cycle: %s", exc)

        # 5. Build Context & Get Versioned Prompt
        # Real account, positions, open orders and market status for the model.
        live = (
            await fetch_live_account_data(db, agent)
            if isinstance(account_data, Unset)
            else account_data
        )
        context = build_agent_context(
            symbols=symbols,
            bars_per_symbol=bars_per_symbol,
            account_info=live.account if live else None,
            positions=live.positions if live else None,
            open_orders=live.open_orders if live else None,
            market_open=live.market_open if live else None,
            strategy_version=agent.strategy_version,
            account_data_status="LIVE" if live else "UNAVAILABLE",
            max_data_age_seconds=get_settings().max_market_data_age_minutes * 60,
        )
        context["versions"] = {
            "strategy": agent.strategy_version,
            "policy": POLICY_VERSION,
        }
        # Scope this cycle to one symbol (watchlist sweep); the prompt decides for it.
        if target_symbol is not None:
            context["decision_target"] = target_symbol

        settings = get_settings()
        model_window_tokens = (
            settings.ollama_num_ctx if settings.llm_provider.lower() == "ollama" else 128000
        )
        selected_strategy = agent.strategy_version
        selection_reason = "User selected this fixed strategy."
        if agent.strategy_version == DYNAMIC_STRATEGY_VERSION:
            selected_strategy, selection_reason = _select_dynamic_strategy(
                context,
                model_window_tokens=model_window_tokens,
            )
        prompt_definition = get_prompt_definition(selected_strategy)
        system_prompt = prompt_definition.text
        context["strategy_selection"] = {
            "mode": "DYNAMIC" if agent.strategy_version == DYNAMIC_STRATEGY_VERSION else "FIXED",
            "selected_strategy": selected_strategy,
            "reason": selection_reason,
        }
        context["strategy_version"] = selected_strategy
        context["versions"]["strategy"] = selected_strategy
        context["knowledge"] = retrieve_knowledge(
            strategy_version=selected_strategy,
            market=context.get("market", {}),
        )
        cycle.prompt_version = selected_strategy
        cycle.prompt_revision = prompt_definition.revision
        context = _enforce_context_window(
            context,
            system_prompt,
            model_window_tokens=model_window_tokens,
        )
        prompt_input = json.dumps(context, separators=(",", ":"))
        market_context = context.get("market", {})
        bars_used = sum(
            int(symbol_context.get("bar_count", 0))
            for symbol_context in market_context.values()
        )
        context_chars = len(prompt_input)
        system_chars = len(system_prompt)
        estimated_tokens = (context_chars + system_chars + 3) // 4
        context["observability"] = {
            "context_chars": context_chars,
            "system_prompt_chars": system_chars,
            "estimated_input_tokens": estimated_tokens,
            "model_window_tokens": model_window_tokens,
            "remaining_context_tokens": max(model_window_tokens - estimated_tokens, 0),
            "context_over_limit": estimated_tokens > model_window_tokens,
            "symbols": len(symbols),
            "bars_used": bars_used,
            "account_data_status": context["account"]["data_status"],
            "context_truncated": bool(
                context.get("context_window_guard", {}).get("truncated")
            ),
        }
        logger.info(
            "Prepared agent context for cycle %s: %s chars, ~%s tokens, %s bars, model window=%s",
            cycle.id,
            context_chars + system_chars,
            context["observability"]["estimated_input_tokens"],
            bars_used,
            model_window_tokens,
        )

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
                cycle.policy_result_json = persistable_policy_result(policy_preview)
            else:
                cycle.policy_decision = PolicyDecisionType.NOT_EVALUATED
            approval = CycleApproval(
                cycle_id=cycle.id,
                status=ApprovalStatus.PENDING,
                proposal_hash=proposal_hash,
                expires_at=utcnow() + timedelta(minutes=get_settings().approval_expiry_minutes),
                policy_version=policy_preview.policy_version if policy_preview else None,
                policy_result_json=(
                    persistable_policy_result(policy_preview) if policy_preview else None
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
            # The signed policy token stays in memory only; it is never stored (#175).
            context["policy_evaluation"] = persistable_policy_result(policy_decision)
            # Persist the same decision the execution gate just produced (#121) - this is
            # a read-only copy for audit/query, not a second evaluation.
            cycle.policy_decision = policy_decision.decision
            cycle.policy_result_json = persistable_policy_result(policy_decision)
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
    once, before the bar prefetch and the live-account read; if the agent already has
    a cycle in progress, CycleOverlapError propagates and no symbols run, without a
    single market-data or broker call (#178). The per-symbol cycles skip the gate, so
    an approval one symbol parks never blocks the next. A failing symbol is logged (its
    cycle is persisted as FAILED) and the sweep continues; if every symbol fails, the
    last error is re-raised so the scheduler counts the agent as failed.
    """
    agent_res = await db.execute(select(AgentConfig).where(AgentConfig.id == agent_id))
    agent = agent_res.scalar_one_or_none()
    if agent is None:
        raise ValueError(f"AgentConfig with id '{agent_id}' not found")

    # Canonical, de-duplicated symbols (the same helper the per-symbol cycle uses), so
    # the scope guard compares one consistent form and one sweep never evaluates (or
    # orders) the same symbol twice.
    symbols = normalize_watchlist(agent.watchlist)
    if not symbols:
        return []

    # Gate once per sweep, before any market-data or broker read: a busy agent (e.g.
    # parked in PENDING_APPROVAL) raises CycleOverlapError here and costs no calls.
    await _enforce_no_active_cycle(db, agent)

    # Fetch bars once for the whole watchlist; per-symbol cycles reuse them.
    bars_per_symbol: dict[str, list[Any]] = {}
    if collector is None:
        try:
            collector = create_market_data_collector()
        except ValueError:
            collector = None
    if collector:
        try:
            settings = get_settings()
            bars_per_symbol = await collector.fetch_latest_bars(
                symbols,
                limit=settings.default_bar_limit,
                lookback_days=30,
                validate=False,
            )
        except Exception as exc:
            logger.warning("Watchlist sweep: bar prefetch failed: %s", exc)
            bars_per_symbol = {}

    # Read the live account once per tick; every per-symbol cycle reuses it.
    live_account_data = await fetch_live_account_data(db, agent)

    cycles: list[AgentCycle] = []
    last_error: Exception | None = None
    for symbol in symbols:
        try:
            cycle = await _run_agent_cycle(
                agent_id,
                db,
                trigger=trigger,
                llm_client=llm_client,
                collector=collector,
                target_symbol=symbol,
                bars_per_symbol=bars_per_symbol,
                account_data=live_account_data,
                # The sweep already ran the gate; the symbols are sequential and must
                # not block on an approval/execution an earlier symbol left active.
                enforce_no_overlap=False,
            )
            cycles.append(cycle)
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