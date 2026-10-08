"""Agent Cycle Orchestrator Engine.

Orchestrates automated and manual agent cycles:
  1. Creates an `AgentCycle` record in PostgreSQL (`PENDING`).
  2. Collects OHLCV market bars for the watchlist and calculates indicators.
  3. Builds sanitized context via `build_agent_context`.
  4. Retrieves the versioned system prompt (`S-001`).
  5. Queries the configured LLM client (Ollama / OpenAI via `get_llm_client`).
  6. Validates the decision output via `validate_decision_proposal`.
  7. On JSON/schema validation error, attempts 1 self-repair prompt retry.
  8. If self-repair also fails, defaults safely to a `HOLD` proposal.
  9. Persists complete cycle state (`snapshot_json`, `prompt_text`, `llm_raw_response`,
     `proposal_json`) to PostgreSQL and marks status `SUCCESS` or `FAILED`.
"""

import json
import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import build_agent_context
from app.agent.prompt import get_system_prompt
from app.agent.tool_validation import ToolValidationError, validate_decision_proposal
from app.db.enums import CycleStatus, CycleTrigger, DecisionAction, Symbol
from app.db.models import AgentConfig, AgentCycle, utcnow
from app.llm.base import LLMClient
from app.llm.factory import get_llm_client
from app.market_data.alpaca_client import MarketDataCollector, create_market_data_collector
from app.schemas import DecisionProposal

logger = logging.getLogger(__name__)


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

    # 2. Create AgentCycle record (PENDING)
    cycle = AgentCycle(
        agent_id=agent.id,
        trigger=trigger,
        status=CycleStatus.PENDING,
        started_at=utcnow(),
        correlation_id=uuid.uuid4(),
    )
    db.add(cycle)
    await db.commit()
    await db.refresh(cycle)

    try:
        # 3. Collect Market Data
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

        # 4. Build Context & Get Versioned Prompt
        context = build_agent_context(
            symbols=agent.watchlist,
            bars_per_symbol=bars_per_symbol,
            strategy_version=agent.strategy_version,
        )

        system_prompt = get_system_prompt(agent.strategy_version)
        prompt_input = json.dumps(context, indent=2)

        # 5. Execute LLM Call
        if llm_client is None:
            llm_client = get_llm_client()

        raw_response = await llm_client.generate(prompt_input, system=system_prompt)

        # 6. Validate Output with 1-Retry Self-Repair
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
            try:
                repaired_response = await llm_client.generate(repair_prompt, system=system_prompt)
                raw_response = repaired_response
                proposal = validate_decision_proposal(repaired_response)
            except ToolValidationError as repair_err:
                logger.error("Self-repair retry also failed schema validation: %s. Defaulting to HOLD.", repair_err)
                default_symbol = Symbol(agent.watchlist[0]) if agent.watchlist else Symbol.AAPL
                proposal = DecisionProposal(
                    symbol=default_symbol,
                    action=DecisionAction.HOLD,
                    quantity=1,
                    reasoning=f"LLM output validation failed twice; defaulted to HOLD. Error: {repair_err}",
                )

        # 7. Persist Cycle Success & Snapshot State
        cycle.snapshot_json = context
        cycle.prompt_text = system_prompt
        cycle.llm_raw_response = raw_response
        cycle.proposal_json = proposal.model_dump(mode="json")
        cycle.status = CycleStatus.COMPLETED
        cycle.completed_at = utcnow()

        await db.commit()
        await db.refresh(cycle)
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
