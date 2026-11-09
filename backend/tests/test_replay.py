import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.replay import ReplaySample, evaluate_replay
from app.market_data.alpaca_client import OHLCVBar


def _bars(start: float = 100.0) -> list[OHLCVBar]:
    return [
        OHLCVBar(
            timestamp=datetime.now(timezone.utc).isoformat(),
            open=price,
            high=price + 1,
            low=price - 1,
            close=price,
            volume=1000,
        )
        for price in [start + index * 0.1 for index in range(50)]
    ]


@pytest.mark.asyncio
async def test_replay_reports_decisions_and_one_step_return():
    llm = MagicMock()
    llm.generate = AsyncMock(
        return_value=json.dumps(
            {
                "symbol": "AAPL",
                "action": "BUY",
                "quantity": 1,
                "reasoning": "Test fixture",
            }
        )
    )
    sample = ReplaySample(
        symbols=["AAPL"],
        bars_per_symbol={"AAPL": _bars()},
        account_info={"cash": "1000", "equity": "1000", "buying_power": "1000"},
        next_prices={"AAPL": 110.0},
    )

    metrics = await evaluate_replay(
        [sample],
        strategy_version="S-002",
        llm_factory=lambda: llm,
    )

    assert metrics.valid_decisions == 1
    assert metrics.invalid_decisions == 0
    assert metrics.action_counts == {"BUY": 1, "SELL": 0, "HOLD": 0}
    assert metrics.cumulative_return_pct > 0
    assert metrics.buy_and_hold_return_pct > 0


@pytest.mark.asyncio
async def test_replay_counts_invalid_outputs_without_failing():
    llm = MagicMock()
    llm.generate = AsyncMock(return_value="not json")
    sample = ReplaySample(symbols=["AAPL"], bars_per_symbol={"AAPL": _bars()})

    metrics = await evaluate_replay(
        [sample],
        strategy_version="S-001",
        llm_factory=lambda: llm,
    )

    assert metrics.valid_decisions == 0
    assert metrics.invalid_decisions == 1
