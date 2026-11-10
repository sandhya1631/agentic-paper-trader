from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Protocol

import httpx

from app.broker.schemas import (
    AlpacaAccountRead,
    AlpacaOrderRead,
    AlpacaPositionRead,
    MarketSnapshotRead,
    OrderCreate,
)
from app.core.constants import (
    ALPACA_DATA_BASE_URL,
    ALPACA_DATA_FEED,
    ALPACA_ORDERS_MAX_PAGES,
    ALPACA_ORDERS_PAGE_LIMIT,
    ALPACA_PAPER_BASE_URL,
)
from app.core.paper_guard import ensure_alpaca_paper_url
from app.market_data.indicators import calculate_indicators


def _optional_decimal(value: Any) -> Decimal | None:
    """Convert a numeric value to Decimal, preserving None for absent values."""
    return Decimal(str(value)) if value is not None else None


def _parse_order(item: dict[str, Any]) -> AlpacaOrderRead:
    """Parse one Alpaca order object.

    A notional (dollar-amount) order comes back with ``qty: null`` and a ``notional``
    amount (#177), so both are optional; ``filled_qty`` defaults to 0 when absent.
    """
    filled_qty = _optional_decimal(item.get("filled_qty"))
    return AlpacaOrderRead(
        id=item["id"],
        client_order_id=item["client_order_id"],
        symbol=item["symbol"],
        qty=_optional_decimal(item.get("qty")),
        notional=_optional_decimal(item.get("notional")),
        filled_qty=filled_qty if filled_qty is not None else Decimal("0"),
        average_fill_price=_optional_decimal(item.get("filled_avg_price")),
        side=item["side"],
        type=item["type"],
        time_in_force=item["time_in_force"],
        status=item["status"],
        submitted_at=item.get("submitted_at"),
        filled_at=item.get("filled_at"),
    )


class AlpacaClient:
    """Asynchronous client interacting with Alpaca REST API."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = ALPACA_PAPER_BASE_URL,
        data_base_url: str = ALPACA_DATA_BASE_URL,
        client: httpx.AsyncClient | None = None,
    ):
        # Normalize base URL (strip trailing /v2 if passed by user)
        clean_base_url = base_url.rstrip("/")
        if clean_base_url.endswith("/v2"):
            clean_base_url = clean_base_url[:-3]

        # Paper only (#70): a live-money URL can never build a client.
        self.base_url = ensure_alpaca_paper_url(clean_base_url)
        self.data_base_url = data_base_url.rstrip("/")
        self.headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
            "Content-Type": "application/json",
        }
        self._client = client
        self._owns_client = client is None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(headers=self.headers, timeout=10.0)
            self._owns_client = True
        return self._client

    async def close(self) -> None:
        if self._owns_client and self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> "AlpacaClient":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def get_account(self) -> AlpacaAccountRead:
        """Fetch paper trading account overview."""
        url = f"{self.base_url}/v2/account"
        client = self._get_client()
        resp = await client.get(url, headers=self.headers, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()

        return AlpacaAccountRead(
            id=data["id"],
            status=data["status"],
            currency=data.get("currency", "USD"),
            cash=Decimal(str(data["cash"])),
            portfolio_value=Decimal(str(data["portfolio_value"])),
            buying_power=Decimal(str(data["buying_power"])),
            equity=Decimal(str(data["equity"])),
            long_market_value=Decimal(str(data.get("long_market_value", 0))),
            short_market_value=Decimal(str(data.get("short_market_value", 0))),
            initial_margin=Decimal(str(data.get("initial_margin", 0))),
            maintenance_margin=Decimal(str(data.get("maintenance_margin", 0))),
            daytrading_buying_power=Decimal(str(data.get("daytrading_buying_power", 0))),
            regt_buying_power=Decimal(str(data.get("regt_buying_power", 0))),
            last_equity=Decimal(str(data.get("last_equity", 0))),
            daily_pnl=Decimal(str(data["equity"])) - Decimal(str(data.get("last_equity", 0))),
            daily_pnl_percent=(
                (
                    Decimal(str(data["equity"])) - Decimal(str(data.get("last_equity", 0)))
                )
                / Decimal(str(data["last_equity"]))
                * Decimal("100")
                if data.get("last_equity") not in (None, "0", 0)
                else None
            ),
            is_paper=True,
        )

    async def get_positions(self) -> list[AlpacaPositionRead]:
        """Fetch open paper positions."""
        url = f"{self.base_url}/v2/positions"
        client = self._get_client()
        resp = await client.get(url, headers=self.headers, timeout=10.0)
        resp.raise_for_status()
        items = resp.json()

        positions: list[AlpacaPositionRead] = []
        for item in items:
            positions.append(
                AlpacaPositionRead(
                    symbol=item["symbol"],
                    qty=Decimal(str(item["qty"])),
                    side=item["side"],
                    market_value=Decimal(str(item["market_value"])),
                    cost_basis=Decimal(str(item["cost_basis"])),
                    unrealized_pl=Decimal(str(item["unrealized_pl"])),
                    unrealized_plpc=Decimal(str(item["unrealized_plpc"])),
                    current_price=Decimal(str(item["current_price"])),
                    avg_entry_price=Decimal(str(item["avg_entry_price"])),
                )
            )
        return positions

    async def get_bars(
        self,
        symbol: str,
        timeframe: str = "5Min",
        limit: int = 50,
        *,
        lookback_days: int = 30,
    ) -> list[dict[str, Any]]:
        """Fetch stock price bars from Alpaca Market Data API.

        A 30-day lookback window (not just "today") ensures enough bars are
        returned early in the trading session, and across weekends/holidays,
        to fulfill `limit` - without a `start`, Alpaca only returns bars from
        the current session so far, which can be far short of `limit`.

        `sort=desc` makes Alpaca return the **newest** `limit` bars in the
        window (newest-first); without it the default ascending order returns
        the *oldest* bars in the 30-day window, making `bars[-1]` ~30 days
        stale and failing the P-004 freshness policy. We reverse the result
        back to chronological (oldest->newest) so downstream code — indicators
        and `bars[-1]` as "latest" — behaves unchanged.
        """
        url = f"{self.data_base_url}/v2/stocks/bars"
        start = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        params = {
            "symbols": symbol.upper(),
            "timeframe": timeframe,
            "limit": limit,
            "feed": ALPACA_DATA_FEED,
            "start": start.isoformat(),
            "sort": "desc",
        }
        client = self._get_client()
        resp = await client.get(url, headers=self.headers, params=params, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()

        symbol_bars = data.get("bars", {}).get(symbol.upper(), [])
        # Alpaca returned newest-first (sort=desc); restore chronological order
        # (oldest->newest) so bars[-1] is the latest bar and indicators compute in order.
        symbol_bars.reverse()
        return symbol_bars

    async def get_market_snapshot(self, symbol: str) -> MarketSnapshotRead:
        """Fetch latest price snapshot and compute 14-period RSI, 20- and 50-period SMA."""
        bars = await self.get_bars(symbol, timeframe="5Min", limit=75)
        if not bars:
            raise ValueError(f"No price bar data available for symbol '{symbol}'")

        close_prices = [float(bar["c"]) for bar in bars]
        latest_close = Decimal(str(close_prices[-1]))
        raw_timestamp = bars[-1].get("t")
        latest_timestamp = str(raw_timestamp) if raw_timestamp is not None else None

        indicators = calculate_indicators(close_prices)

        return MarketSnapshotRead(
            symbol=symbol.upper(),
            latest_close=latest_close,
            rsi_14=_optional_decimal(indicators.get("rsi_14")),
            sma_20=_optional_decimal(indicators.get("sma_20")),
            sma_50=_optional_decimal(indicators.get("sma_50")),
            bars_count=len(bars),
            timestamp=latest_timestamp,
        )

    async def submit_order(self, order: OrderCreate) -> AlpacaOrderRead:
        """Submit a paper trading order to Alpaca."""
        url = f"{self.base_url}/v2/orders"
        payload = {
            "symbol": order.symbol.upper(),
            "qty": str(order.qty),
            "side": order.side.value.lower(),
            "type": order.type.value.lower(),
            "time_in_force": order.time_in_force.value.lower(),
        }
        if order.type.value.lower() == "limit" and order.limit_price is not None:
            payload["limit_price"] = str(order.limit_price)
        if order.client_order_id:
            payload["client_order_id"] = order.client_order_id

        client = self._get_client()
        resp = await client.post(url, headers=self.headers, json=payload, timeout=10.0)
        resp.raise_for_status()
        return _parse_order(resp.json())

    async def get_orders(
        self,
        status: str = "all",
        limit: int = 50,
        *,
        after: datetime | None = None,
        until: datetime | None = None,
        direction: str | None = None,
    ) -> list[AlpacaOrderRead]:
        """Fetch one page of paper orders (Alpaca allows ``limit`` up to 500).

        ``after`` / ``until`` keep only orders submitted after / until that moment (both
        exclusive at Alpaca); ``direction`` is ``"asc"`` or ``"desc"`` (Alpaca's default).
        Use ``fetch_all_orders`` when every matching order must be seen.
        """
        url = f"{self.base_url}/v2/orders"
        params: dict[str, Any] = {"status": status, "limit": limit}
        if after is not None:
            params["after"] = after.isoformat()
        if until is not None:
            params["until"] = until.isoformat()
        if direction is not None:
            params["direction"] = direction
        client = self._get_client()
        resp = await client.get(url, headers=self.headers, params=params, timeout=10.0)
        resp.raise_for_status()
        return [_parse_order(item) for item in resp.json()]

    async def get_order_by_client_order_id(self, client_order_id: str) -> AlpacaOrderRead | None:
        """Fetch a single order by client_order_id, or None if Alpaca has no record of it."""
        url = f"{self.base_url}/v2/orders:by_client_order_id"
        params = {"client_order_id": client_order_id}
        client = self._get_client()
        resp = await client.get(url, headers=self.headers, params=params, timeout=10.0)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return _parse_order(resp.json())


class OrderLister(Protocol):
    """Anything with AlpacaClient.get_orders' signature (the client, or a test double)."""

    async def get_orders(
        self,
        status: str = "all",
        limit: int = 50,
        *,
        after: datetime | None = None,
        until: datetime | None = None,
        direction: str | None = None,
    ) -> list[AlpacaOrderRead]: ...


def _submitted_at(order: AlpacaOrderRead) -> datetime:
    if not order.submitted_at:
        raise ValueError("An order without submitted_at prevents paging the order list")
    try:
        moment = datetime.fromisoformat(order.submitted_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Unreadable order timestamp: {order.submitted_at!r}") from exc
    if moment.tzinfo is None:
        raise ValueError(f"Order timestamp has no timezone: {order.submitted_at!r}")
    return moment


async def fetch_all_orders(
    client: OrderLister,
    *,
    status: str,
    after: datetime | None = None,
) -> list[AlpacaOrderRead]:
    """Fetch EVERY order matching ``status`` (and submitted after ``after``), newest first.

    The risk policies must not judge from a truncated list (#177), but Alpaca returns at
    most ALPACA_ORDERS_PAGE_LIMIT orders per request and has no cursor. A full page is
    therefore followed by a request for the orders submitted until the oldest one seen.
    ``until`` is exclusive, so it is nudged one microsecond later: orders that share that
    timestamp are read again (and dropped by id) instead of being skipped.

    Fails closed: raises ValueError when completeness cannot be shown (a full page with
    an unreadable ``submitted_at``, a full page that adds nothing new, or more than
    ALPACA_ORDERS_MAX_PAGES full pages).
    """
    page = await client.get_orders(
        status=status, limit=ALPACA_ORDERS_PAGE_LIMIT, after=after, direction="desc"
    )
    orders = list(page)
    seen = {order.id for order in orders} if len(page) >= ALPACA_ORDERS_PAGE_LIMIT else set()
    for _ in range(ALPACA_ORDERS_MAX_PAGES - 1):
        if len(page) < ALPACA_ORDERS_PAGE_LIMIT:
            return orders
        until = min(_submitted_at(order) for order in page) + timedelta(microseconds=1)
        page = await client.get_orders(
            status=status,
            limit=ALPACA_ORDERS_PAGE_LIMIT,
            after=after,
            until=until,
            direction="desc",
        )
        new = [order for order in page if order.id not in seen]
        if len(page) >= ALPACA_ORDERS_PAGE_LIMIT and not new:
            raise ValueError("Paging the Alpaca order list made no progress")
        orders.extend(new)
        seen.update(order.id for order in new)
    if len(page) < ALPACA_ORDERS_PAGE_LIMIT:
        return orders
    raise ValueError(
        f"More than {ALPACA_ORDERS_PAGE_LIMIT * ALPACA_ORDERS_MAX_PAGES} matching orders; "
        "refusing to use a partial order list"
    )
