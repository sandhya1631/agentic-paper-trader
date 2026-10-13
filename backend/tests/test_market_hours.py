"""Tests for market-hours validation (Story 3.1.4 / Issue #31)."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.risk.market_hours import (
    MarketClock,
    check_market_open,
    execute_with_market_hours_check,
    fetch_market_clock,
    parse_clock,
    validate_market_hours,
)

OPEN_JSON = {
    "timestamp": "2026-10-13T11:30:12.123-04:00",
    "is_open": True,
    "next_open": "2026-10-14T09:30:00-04:00",
    "next_close": "2026-10-13T16:00:00-04:00",
}
CLOSED_JSON = {**OPEN_JSON, "is_open": False}


def _mock_http(json_body):
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = json_body
    http = AsyncMock(spec=httpx.AsyncClient)
    http.get = AsyncMock(return_value=response)
    return http


def _clock_fetcher(json_body):
    async def fetch() -> MarketClock:
        return parse_clock(json_body)

    return fetch


# --- parsing and pure check ---


def test_parse_clock_open():
    clock = parse_clock(OPEN_JSON)
    assert clock.is_open is True
    assert clock.next_open is not None


def test_check_market_open_allows_when_open():
    result = check_market_open(parse_clock(OPEN_JSON))
    assert result.allowed is True


def test_check_market_open_rejects_when_closed():
    result = check_market_open(parse_clock(CLOSED_JSON))
    assert result.allowed is False
    assert "closed" in result.reason.lower()


# --- fetching the clock ---


@pytest.mark.asyncio
async def test_fetch_market_clock_calls_clock_endpoint():
    http = _mock_http(OPEN_JSON)

    clock = await fetch_market_clock(
        "https://paper-api.alpaca.markets", {"APCA-API-KEY-ID": "k"}, http=http
    )

    assert clock.is_open is True
    url = http.get.call_args.args[0]
    assert url == "https://paper-api.alpaca.markets/v2/clock"


# --- reject BEFORE broker submission ---


@pytest.mark.asyncio
async def test_closed_market_never_calls_broker():
    submit = AsyncMock(return_value="order")

    result, order = await execute_with_market_hours_check(
        _clock_fetcher(CLOSED_JSON), submit
    )

    assert result.allowed is False
    assert order is None
    submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_open_market_calls_broker():
    submit = AsyncMock(return_value="order")

    result, order = await execute_with_market_hours_check(
        _clock_fetcher(OPEN_JSON), submit
    )

    assert result.allowed is True
    assert order == "order"
    submit.assert_awaited_once()


@pytest.mark.asyncio
async def test_clock_failure_fails_closed():
    async def broken_fetch() -> MarketClock:
        raise httpx.ConnectError("alpaca down")

    submit = AsyncMock(return_value="order")

    result, order = await execute_with_market_hours_check(broken_fetch, submit)

    assert result.allowed is False
    assert order is None
    assert "could not verify" in result.reason.lower()
    submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_validate_market_hours_open():
    result = await validate_market_hours(_clock_fetcher(OPEN_JSON))
    assert result.allowed is True