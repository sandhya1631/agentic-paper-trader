from decimal import Decimal
from typing import Any

import httpx

from app.broker.schemas import (
    AlpacaAccountRead,
    AlpacaOrderRead,
    AlpacaPositionRead,
    MarketSnapshotRead,
    OrderCreate,
)


def calculate_sma(close_prices: list[Decimal], period: int = 20) -> Decimal | None:
    """Calculate Simple Moving Average (SMA)."""
    if len(close_prices) < period:
        return None
    recent = close_prices[-period:]
    return sum(recent) / Decimal(period)


def calculate_rsi(close_prices: list[Decimal], period: int = 14) -> Decimal | None:
    """Calculate Relative Strength Index (RSI) using Wilder's smoothing method."""
    if len(close_prices) <= period:
        return None

    gains: list[Decimal] = []
    losses: list[Decimal] = []

    for i in range(1, len(close_prices)):
        change = close_prices[i] - close_prices[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(Decimal("0"))
        else:
            gains.append(Decimal("0"))
            losses.append(abs(change))

    if len(gains) < period:
        return None

    avg_gain = sum(gains[:period]) / Decimal(period)
    avg_loss = sum(losses[:period]) / Decimal(period)

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * Decimal(period - 1) + gains[i]) / Decimal(period)
        avg_loss = (avg_loss * Decimal(period - 1) + losses[i]) / Decimal(period)

    if avg_loss == 0:
        return Decimal("100")

    rs = avg_gain / avg_loss
    rsi = Decimal("100") - (Decimal("100") / (Decimal("1") + rs))
    return round(rsi, 2)


class AlpacaClient:
    """Asynchronous client interacting with Alpaca REST API."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = "https://paper-api.alpaca.markets",
        data_base_url: str = "https://data.alpaca.markets",
    ):
        # Normalize base URL (strip trailing /v2 if passed by user)
        clean_base_url = base_url.rstrip("/")
        if clean_base_url.endswith("/v2"):
            clean_base_url = clean_base_url[:-3]

        self.base_url = clean_base_url
        self.data_base_url = data_base_url.rstrip("/")
        self.headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
            "Content-Type": "application/json",
        }

    async def get_account(self) -> AlpacaAccountRead:
        """Fetch paper trading account overview."""
        url = f"{self.base_url}/v2/account"
        async with httpx.AsyncClient() as client:
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
            is_paper=True,
        )

    async def get_positions(self) -> list[AlpacaPositionRead]:
        """Fetch open paper positions."""
        url = f"{self.base_url}/v2/positions"
        async with httpx.AsyncClient() as client:
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
        self, symbol: str, timeframe: str = "5Min", limit: int = 50
    ) -> list[dict[str, Any]]:
        """Fetch stock price bars from Alpaca Market Data API."""
        url = f"{self.data_base_url}/v2/stocks/bars"
        params = {
            "symbols": symbol.upper(),
            "timeframe": timeframe,
            "limit": limit,
            "feed": "iex",
        }
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=self.headers, params=params, timeout=10.0)
            resp.raise_for_status()
            data = resp.json()

        symbol_bars = data.get("bars", {}).get(symbol.upper(), [])
        return symbol_bars

    async def get_market_snapshot(self, symbol: str) -> MarketSnapshotRead:
        """Fetch latest price snapshot and compute 14-period RSI & 20-period SMA."""
        bars = await self.get_bars(symbol, timeframe="5Min", limit=60)
        if not bars:
            raise ValueError(f"No price bar data available for symbol '{symbol}'")

        close_prices = [Decimal(str(bar["c"])) for bar in bars]
        latest_close = close_prices[-1]
        latest_timestamp = bars[-1].get("t")

        sma_20 = calculate_sma(close_prices, period=20)
        rsi_14 = calculate_rsi(close_prices, period=14)

        return MarketSnapshotRead(
            symbol=symbol.upper(),
            latest_close=latest_close,
            rsi_14=rsi_14,
            sma_20=sma_20,
            bars_count=len(bars),
            timestamp=str(latest_timestamp),
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

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=self.headers, json=payload, timeout=10.0)
            resp.raise_for_status()
            data = resp.json()

        return AlpacaOrderRead(
            id=data["id"],
            client_order_id=data["client_order_id"],
            symbol=data["symbol"],
            qty=Decimal(str(data["qty"])),
            filled_qty=Decimal(str(data.get("filled_qty", 0))),
            side=data["side"],
            type=data["type"],
            time_in_force=data["time_in_force"],
            status=data["status"],
            submitted_at=data.get("submitted_at"),
            filled_at=data.get("filled_at"),
        )

    async def get_orders(self, status: str = "all", limit: int = 50) -> list[AlpacaOrderRead]:
        """Fetch list of paper orders."""
        url = f"{self.base_url}/v2/orders"
        params = {"status": status, "limit": limit}
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=self.headers, params=params, timeout=10.0)
            resp.raise_for_status()
            items = resp.json()

        orders: list[AlpacaOrderRead] = []
        for item in items:
            orders.append(
                AlpacaOrderRead(
                    id=item["id"],
                    client_order_id=item["client_order_id"],
                    symbol=item["symbol"],
                    qty=Decimal(str(item["qty"])),
                    filled_qty=Decimal(str(item.get("filled_qty", 0))),
                    side=item["side"],
                    type=item["type"],
                    time_in_force=item["time_in_force"],
                    status=item["status"],
                    submitted_at=item.get("submitted_at"),
                    filled_at=item.get("filled_at"),
                )
            )
        return orders
