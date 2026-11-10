"""Offline strategy replay and evaluation helpers.

Replay uses the same context builder and versioned prompts as live cycles, but
never touches the database, broker, approval workflow, or order gateway.
"""

import json
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable

from app.agent.context import build_agent_context
from app.agent.prompt import get_prompt_definition
from app.agent.tool_validation import ToolValidationError, validate_decision_proposal
from app.llm.base import LLMClient


@dataclass(frozen=True)
class ReplaySample:
    """One historical decision point and its optional next-price outcome."""

    symbols: list[str]
    bars_per_symbol: dict[str, list[Any]]
    account_info: dict[str, Any] | None = None
    positions: list[dict[str, Any]] | None = None
    open_orders: list[dict[str, Any]] | None = None
    market_open: bool | None = True
    next_prices: dict[str, float] | None = None


@dataclass(frozen=True)
class ReplayMetrics:
    """Aggregated quality and simple one-step performance measurements."""

    strategy_version: str
    samples: int
    valid_decisions: int
    invalid_decisions: int
    action_counts: dict[str, int]
    average_input_tokens: float
    average_latency_ms: float
    cumulative_return_pct: float
    buy_and_hold_return_pct: float


def _decision_return(action: str, current: float, next_price: float) -> float:
    if current <= 0:
        return 0.0
    move = (next_price / current) - 1
    return move if action == "BUY" else -move if action == "SELL" else 0.0


async def evaluate_replay(
    samples: list[ReplaySample],
    *,
    strategy_version: str,
    llm_factory: Callable[[], LLMClient],
) -> ReplayMetrics:
    """Evaluate one strategy against fixed samples without executing trades."""
    prompt = get_prompt_definition(strategy_version).text
    valid = invalid = 0
    action_counts = {"BUY": 0, "SELL": 0, "HOLD": 0}
    total_tokens = total_latency_ms = cumulative_return = buy_hold_return = 0.0

    for sample in samples:
        context = build_agent_context(
            sample.symbols,
            sample.bars_per_symbol,
            account_info=sample.account_info,
            positions=sample.positions,
            open_orders=sample.open_orders,
            strategy_version=strategy_version,
            account_data_status="LIVE" if sample.account_info else "UNAVAILABLE_REPLAY",
            market_open=sample.market_open,
        )
        prompt_input = json.dumps(context, separators=(",", ":"))
        total_tokens += (len(prompt_input) + len(prompt) + 3) / 4
        started = perf_counter()
        raw = await llm_factory().generate(prompt_input, system=prompt)
        total_latency_ms += (perf_counter() - started) * 1000
        try:
            proposal = validate_decision_proposal(raw)
        except ToolValidationError:
            invalid += 1
            continue

        valid += 1
        action = proposal.action.value
        action_counts[action] += 1
        next_prices = sample.next_prices or {}
        if proposal.symbol in next_prices:
            current = context["market"][proposal.symbol]["latest_price"]
            if current is not None:
                cumulative_return += _decision_return(
                    action, float(current), float(next_prices[proposal.symbol])
                )
                buy_hold_return += (
                    float(next_prices[proposal.symbol]) / float(current) - 1
                )

    count = len(samples) or 1
    return ReplayMetrics(
        strategy_version=strategy_version,
        samples=len(samples),
        valid_decisions=valid,
        invalid_decisions=invalid,
        action_counts=action_counts,
        average_input_tokens=total_tokens / count,
        average_latency_ms=total_latency_ms / count,
        cumulative_return_pct=round(cumulative_return * 100, 4),
        buy_and_hold_return_pct=round(buy_hold_return * 100, 4),
    )
