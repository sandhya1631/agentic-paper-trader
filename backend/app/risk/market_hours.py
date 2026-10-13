"""Market-hours validation (Story 3.1.4 / Issue #31).

Acceptance criteria:
  - The Alpaca market clock is checked.
  - Closed-market proposals are rejected BEFORE broker submission.

Design notes:
  - Fails CLOSED: if the clock cannot be fetched or parsed, the order is
    rejected (we never guess that the market is open).
  - Mirrors risk/allocation.py + risk/execution.py: a pure check function
    plus a wrapper that only calls the broker when the check passes.
  - The existing policy P-003 (policies.py) already reads `market_open`
    from PolicyContext; use `fetch_market_clock_for()` to fill it with real
    data instead of the default `True`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypeVar

import httpx

T = TypeVar("T")


@dataclass(frozen=True)
class MarketClock:
    """Parsed response of Alpaca's GET /v2/clock."""

    is_open: bool
    timestamp: datetime | None = None
    next_open: datetime | None = None
    next_close: datetime | None = None


@dataclass(frozen=True)
class MarketHoursResult:
    """Outcome of the market-hours check."""

    allowed: bool
    reason: str
    clock: MarketClock | None = None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def parse_clock(data: dict[str, Any]) -> MarketClock:
    """Turn the raw Alpaca clock JSON into a MarketClock."""
    return MarketClock(
        is_open=bool(data["is_open"]),
        timestamp=_parse_dt(data.get("timestamp")),
        next_open=_parse_dt(data.get("next_open")),
        next_close=_parse_dt(data.get("next_close")),
    )


async def fetch_market_clock(
    base_url: str,
    headers: dict[str, str],
    http: httpx.AsyncClient | None = None,
) -> MarketClock:
    """Call Alpaca GET /v2/clock. Pass `http` to reuse a client (or to mock)."""
    url = f"{base_url.rstrip('/')}/v2/clock"
    if http is not None:
        resp = await http.get(url, headers=headers, timeout=10.0)
    else:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
    resp.raise_for_status()
    return parse_clock(resp.json())


async def fetch_market_clock_for(alpaca: Any) -> MarketClock:
    """Convenience wrapper: use an existing AlpacaClient's URL and keys."""
    return await fetch_market_clock(alpaca.base_url, alpaca.headers)


def check_market_open(clock: MarketClock) -> MarketHoursResult:
    """Pure check: allowed only if the market is open right now."""
    if clock.is_open:
        return MarketHoursResult(True, "Market is open", clock)

    next_open = clock.next_open.isoformat() if clock.next_open else "unknown"
    return MarketHoursResult(
        False,
        f"Market is closed (next open: {next_open})",
        clock,
    )


async def validate_market_hours(
    fetch_clock: Callable[[], Awaitable[MarketClock]],
) -> MarketHoursResult:
    """Fetch the clock and check it. Any error means reject (fail closed)."""
    try:
        clock = await fetch_clock()
    except Exception as exc:  # noqa: BLE001 - deliberately fail closed
        return MarketHoursResult(
            False,
            f"Could not verify market hours, rejecting order: {exc}",
        )
    return check_market_open(clock)


async def execute_with_market_hours_check(
    fetch_clock: Callable[[], Awaitable[MarketClock]],
    submit_order: Callable[[], Awaitable[T]],
) -> tuple[MarketHoursResult, T | None]:
    """Check market hours before calling the broker (broker never called if closed)."""
    result = await validate_market_hours(fetch_clock)

    if not result.allowed:
        return result, None

    broker_result = await submit_order()
    return result, broker_result