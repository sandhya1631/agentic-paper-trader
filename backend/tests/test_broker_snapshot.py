"""Unit tests for AlpacaClient.get_market_snapshot and the _optional_decimal helper.

Added alongside the Ruff full-scope cleanup (#97), which extracted the inline
``Decimal(str(...)) if ... is not None else None`` ternaries into ``_optional_decimal``.
These lock in that the refactor is behavior-preserving: indicator values are converted
to Decimal exactly, None is preserved, and a falsy-but-present ``0`` is NOT turned into
None. ``get_market_snapshot`` itself had no direct coverage before this file.
"""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.broker.alpaca import AlpacaClient, _optional_decimal


def _mock_http_get(json_body):
    """Build a mocked httpx.AsyncClient whose GET returns the given JSON body."""
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = json_body
    http = AsyncMock(spec=httpx.AsyncClient)
    http.get = AsyncMock(return_value=response)
    http.is_closed = False  # otherwise AlpacaClient._get_client() discards this mock
    return http


# --- _optional_decimal (the extracted helper) ---


def test_optional_decimal_preserves_none():
    assert _optional_decimal(None) is None


def test_optional_decimal_converts_value_exactly():
    assert _optional_decimal(55.3) == Decimal("55.3")


def test_optional_decimal_keeps_zero():
    # 0 is falsy but not None — it must become Decimal('0'), never None.
    result = _optional_decimal(0)
    assert result is not None
    assert result == Decimal("0")


# --- AlpacaClient.get_market_snapshot ---


@pytest.mark.asyncio
async def test_get_market_snapshot_computes_indicators():
    # 60 flat bars → neutral RSI (50) and SMAs equal to the price.
    bars = [{"c": 100.0, "t": f"2024-01-01T00:{i:02d}:00Z"} for i in range(60)]
    http = _mock_http_get({"bars": {"AAPL": bars}})
    client = AlpacaClient(api_key="k", api_secret="s", client=http)

    snapshot = await client.get_market_snapshot("aapl")

    assert snapshot.symbol == "AAPL"
    assert snapshot.latest_close == Decimal("100.0")
    assert snapshot.rsi_14 == Decimal("50.0")
    assert snapshot.sma_20 == Decimal("100.0")
    assert snapshot.sma_50 == Decimal("100.0")
    assert snapshot.bars_count == 60
    assert snapshot.timestamp == "2024-01-01T00:59:00Z"


@pytest.mark.asyncio
async def test_get_market_snapshot_raises_when_no_bars():
    http = _mock_http_get({"bars": {}})
    client = AlpacaClient(api_key="k", api_secret="s", client=http)

    with pytest.raises(ValueError):
        await client.get_market_snapshot("AAPL")


# --- AlpacaClient.get_bars (Issue #124) ---


@pytest.mark.asyncio
async def test_get_bars_requests_a_lookback_window_not_just_today():
    """Without `start`, Alpaca only returns bars from the current session so far,
    which can be far short of `limit` early in the trading day (Issue #124)."""
    http = _mock_http_get({"bars": {"AAPL": []}})
    client = AlpacaClient(api_key="k", api_secret="s", client=http)

    await client.get_bars("AAPL", timeframe="5Min", limit=75, lookback_days=30)

    params = http.get.await_args.kwargs["params"]
    assert "start" in params
    start = datetime.fromisoformat(params["start"])
    assert (datetime.now(timezone.utc) - start).days >= 29


@pytest.mark.asyncio
async def test_get_bars_default_lookback_is_30_days():
    http = _mock_http_get({"bars": {"AAPL": []}})
    client = AlpacaClient(api_key="k", api_secret="s", client=http)

    await client.get_bars("AAPL")

    start = datetime.fromisoformat(http.get.await_args.kwargs["params"]["start"])
    assert (datetime.now(timezone.utc) - start).days >= 29
