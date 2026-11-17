"""Live Alpaca paper, READ-ONLY: account, clock, positions, open orders, bars (#100).

Gated by ALPACA_INTEGRATION=1 plus the env ALPACA_* paper keys. Nothing here submits,
changes or cancels anything. Safe to run at any hour (off-hours the clock is closed
and the latest bars are from the last session).
"""

from datetime import datetime

import pytest
from integration_gates import require_alpaca

from app.broker.alpaca import AlpacaClient
from app.core.config import get_settings
from app.core.constants import ALPACA_PAPER_BASE_URL
from app.market_data.alpaca_client import create_market_data_collector
from app.risk.market_hours import fetch_market_clock_for

pytestmark = pytest.mark.integration


def _client() -> AlpacaClient:
    key, secret = require_alpaca()
    return AlpacaClient(
        api_key=key, api_secret=secret, base_url=get_settings().alpaca_paper_base_url
    )


async def test_paper_account_and_clock_are_readable():
    async with _client() as client:
        assert client.base_url == ALPACA_PAPER_BASE_URL  # the paper guard held
        account = await client.get_account()
        clock = await fetch_market_clock_for(client)

    assert account.equity >= 0
    assert account.cash is not None
    assert isinstance(clock.is_open, bool)
    assert clock.next_open is not None and clock.next_close is not None


async def test_positions_and_open_orders_are_readable():
    async with _client() as client:
        positions = await client.get_positions()
        open_orders = await client.get_orders(status="open")

    assert all(position.symbol for position in positions)
    assert all(order.status for order in open_orders)


async def test_latest_bars_are_fetched_for_a_symbol():
    require_alpaca()
    collector = create_market_data_collector()

    bars = await collector.fetch_latest_bars(
        ["AAPL"], limit=get_settings().default_bar_limit, lookback_days=30, validate=False
    )

    aapl = bars.get("AAPL") or []
    assert aapl, "IEX returned no AAPL bars in the last 30 days"
    assert all(bar.close > 0 for bar in aapl)
    stamps = [datetime.fromisoformat(bar.timestamp.replace("Z", "+00:00")) for bar in aapl]
    # One consistent order (the collector's contract), and no duplicates.
    assert stamps == sorted(stamps) or stamps == sorted(stamps, reverse=True)
    assert len(set(stamps)) == len(stamps)
