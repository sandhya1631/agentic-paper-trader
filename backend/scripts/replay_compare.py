"""Compare every registered strategy on recent market data to choose the production
strategy (#188).

Builds walk-forward replay samples (no look-ahead) from recent Alpaca 5-minute IEX bars
for a candidate watchlist, then scores S-001 through S-007 and DYNAMIC through
`evaluate_replay` with the production LLM, and prints one comparison table.

This never creates cycles, touches the broker, or submits orders. It does make one
read-only Alpaca market-data call and one paid LLM call per sample per strategy — the
`--max-samples` cap below bounds that cost.

Usage (from backend/, with the venv active; needs ALPACA_API_KEY/ALPACA_API_SECRET, plus
whatever LLM_PROVIDER is configured needs: a reachable Ollama server, or OPENAI_API_KEY /
ANTHROPIC_API_KEY):
    python scripts/replay_compare.py --watchlist AAPL NVDA SPY --max-samples 15

The output states plainly what this is: replay skips the policy engine and scores
one-step returns, so it compares decision behavior, not a full backtest.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.agent.context import build_agent_context
from app.agent.orchestrator import _select_dynamic_strategy
from app.agent.prompt import STRATEGY_REGISTRY
from app.agent.replay import ReplayMetrics, ReplaySample, evaluate_replay
from app.core.config import Settings, get_settings
from app.core.constants import DEFAULT_WATCHLIST, DYNAMIC_STRATEGY_VERSION
from app.llm.factory import create_llm_client
from app.market_data.alpaca_client import OHLCVBar, create_market_data_collector

# build_agent_context needs 50 bars for a non-null SMA-50 (matching the live collector
# and the frontend's MIN_REPLAY_BARS, replay-view.ts); one more bar is held back per
# sample as the no-look-ahead outcome.
WINDOW_BARS = 50

# A fixed, clearly-fake paper account so the model reasons with LIVE account data
# instead of defaulting to HOLD on UNAVAILABLE (every prompt's mandated response).
DEMO_ACCOUNT_INFO = {
    "cash": "50000.00",
    "equity": "50000.00",
    "buying_power": "50000.00",
    "daily_pnl": "0.00",
}

# Matches the orchestrator's live-cycle default for a non-Ollama provider
# (`run_watchlist_cycles`, orchestrator.py): Ollama's window is model-specific and
# configured; every other provider's replay model has a context window at least this
# large.
NON_OLLAMA_MODEL_WINDOW_TOKENS = 128_000


def model_window_tokens(settings: Settings) -> int:
    return settings.ollama_num_ctx if settings.llm_provider.lower() == "ollama" else (
        NON_OLLAMA_MODEL_WINDOW_TOKENS
    )


def build_walk_forward_samples(
    symbol: str, bars: list[OHLCVBar], *, window: int = WINDOW_BARS, max_samples: int = 20
) -> list[ReplaySample]:
    """Slide a `window`-bar context one bar at a time; each sample holds back the next
    bar as its outcome, so no sample's context ever includes its own outcome bar.

    Returns at most `max_samples`, the most recent ones, oldest first.
    """
    samples = []
    for end in range(window + 1, len(bars) + 1):
        context_bars = bars[end - window - 1 : end - 1]
        held = bars[end - 1]
        samples.append(
            ReplaySample(
                symbols=[symbol],
                bars_per_symbol={symbol: context_bars},
                account_info=DEMO_ACCOUNT_INFO,
                positions=[],
                open_orders=[],
                market_open=True,
                next_prices={symbol: held.close},
            )
        )
    return samples[-max_samples:]


def _merge_metrics(label: str, parts: list[ReplayMetrics]) -> ReplayMetrics:
    """Combine several (usually single-sample) ReplayMetrics into one, for DYNAMIC,
    where each sample can pick a different registered strategy."""
    total_samples = sum(part.samples for part in parts)
    action_counts = {"BUY": 0, "SELL": 0, "HOLD": 0}
    for part in parts:
        for action, count in part.action_counts.items():
            action_counts[action] += count
    count = total_samples or 1
    return ReplayMetrics(
        strategy_version=label,
        samples=total_samples,
        valid_decisions=sum(part.valid_decisions for part in parts),
        invalid_decisions=sum(part.invalid_decisions for part in parts),
        action_counts=action_counts,
        average_input_tokens=sum(p.average_input_tokens * p.samples for p in parts) / count,
        average_latency_ms=sum(p.average_latency_ms * p.samples for p in parts) / count,
        cumulative_return_pct=round(sum(part.cumulative_return_pct for part in parts), 4),
        buy_and_hold_return_pct=round(
            sum(part.buy_and_hold_return_pct for part in parts), 4
        ),
    )


async def evaluate_dynamic_replay(
    samples: list[ReplaySample],
    *,
    model_window_tokens: int,
    llm_factory,
) -> ReplayMetrics:
    """DYNAMIC picks a registered strategy per cycle (orchestrator._select_dynamic_
    strategy); replay that choice per sample, then combine into one result."""
    per_sample: list[ReplayMetrics] = []
    for sample in samples:
        context = build_agent_context(
            sample.symbols,
            sample.bars_per_symbol,
            account_info=sample.account_info,
            positions=sample.positions,
            open_orders=sample.open_orders,
            strategy_version=DYNAMIC_STRATEGY_VERSION,
            account_data_status="LIVE" if sample.account_info else "UNAVAILABLE_REPLAY",
            market_open=sample.market_open,
        )
        selected_version, _reason = _select_dynamic_strategy(
            context, model_window_tokens=model_window_tokens
        )
        per_sample.append(
            await evaluate_replay(
                [sample], strategy_version=selected_version, llm_factory=llm_factory
            )
        )
    return _merge_metrics(DYNAMIC_STRATEGY_VERSION, per_sample)


async def run_comparison(
    watchlist: list[str], *, max_samples: int, llm_factory
) -> list[ReplayMetrics]:
    settings = get_settings()
    collector = create_market_data_collector(settings)
    bars_per_symbol = await collector.fetch_latest_bars(
        watchlist,
        limit=WINDOW_BARS + max_samples,
        timeframe_minutes=5,
        lookback_days=30,
    )

    samples: list[ReplaySample] = []
    for symbol in watchlist:
        samples.extend(
            build_walk_forward_samples(symbol, bars_per_symbol[symbol], max_samples=max_samples)
        )
    # Same fixed sample set for every strategy, for a fair comparison; also the hard cap
    # on samples per strategy.
    samples = samples[-max_samples:]
    if not samples:
        raise SystemExit(
            f"Not enough bars to build a sample (need {WINDOW_BARS + 1}+ per symbol)."
        )

    results = []
    for strategy_version in STRATEGY_REGISTRY:
        results.append(
            await evaluate_replay(
                samples, strategy_version=strategy_version, llm_factory=llm_factory
            )
        )
    results.append(
        await evaluate_dynamic_replay(
            samples,
            model_window_tokens=model_window_tokens(get_settings()),
            llm_factory=llm_factory,
        )
    )
    return results


def print_comparison(results: list[ReplayMetrics]) -> None:
    print(
        "Replay skips the policy engine and scores one-step returns: this compares "
        "decision behavior, not a full backtest.\n"
    )
    header = (
        f"{'strategy':10} {'samples':>7} {'valid%':>7} {'BUY':>4} {'SELL':>4} {'HOLD':>4} "
        f"{'avg_tok':>8} {'avg_ms':>8} {'return%':>8} {'b&h%':>8}"
    )
    print(header)
    print("-" * len(header))
    for metrics in results:
        valid_pct = 100 * metrics.valid_decisions / metrics.samples if metrics.samples else 0.0
        print(
            f"{metrics.strategy_version:10} {metrics.samples:>7} {valid_pct:>6.1f}% "
            f"{metrics.action_counts['BUY']:>4} {metrics.action_counts['SELL']:>4} "
            f"{metrics.action_counts['HOLD']:>4} {metrics.average_input_tokens:>8.0f} "
            f"{metrics.average_latency_ms:>8.0f} {metrics.cumulative_return_pct:>8.2f} "
            f"{metrics.buy_and_hold_return_pct:>8.2f}"
        )


def build_llm_factory(settings: Settings):
    """A fresh client per call, for whichever LLM_PROVIDER is configured (#188):
    same provider the production deployment would use for a live cycle."""
    return lambda: create_llm_client(settings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--watchlist", nargs="+", default=DEFAULT_WATCHLIST)
    parser.add_argument(
        "--max-samples",
        type=int,
        default=15,
        help="hard cap on samples per strategy, shared across the whole watchlist",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    llm_factory = build_llm_factory(settings)

    results = asyncio.run(
        run_comparison(args.watchlist, max_samples=args.max_samples, llm_factory=llm_factory)
    )
    print_comparison(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
