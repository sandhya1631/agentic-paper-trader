"""Agent Context Builder.

Compiles structured market data, technical indicators (RSI-14, SMA-20, SMA-50),
portfolio cash/equity, open positions, and open orders into a sanitized context
dictionary for the LLM. Strips all API keys, secrets, and credential tokens.
"""

from typing import Any

from app.market_data.indicators import calculate_indicators


def build_agent_context(
    symbols: list[str],
    bars_per_symbol: dict[str, list[Any]],
    account_info: dict[str, Any] | None = None,
    positions: list[dict[str, Any]] | None = None,
    open_orders: list[dict[str, Any]] | None = None,
    strategy_version: str = "S-001",
) -> dict[str, Any]:
    """Assemble sanitized market and account context for LLM prompt generation."""
    market_context: dict[str, Any] = {}

    for sym in symbols:
        bars = bars_per_symbol.get(sym, [])
        if not bars:
            market_context[sym] = {
                "latest_price": None,
                "rsi_14": None,
                "sma_20": None,
                "sma_50": None,
                "bar_count": 0,
                "status": "NO_DATA",
            }
            continue

        latest_bar = bars[-1]
        latest_price = getattr(latest_bar, "close", None)
        if latest_price is None and isinstance(latest_bar, dict):
            latest_price = latest_bar.get("close")

        latest_ts = getattr(latest_bar, "timestamp", None)
        if latest_ts is None and isinstance(latest_bar, dict):
            latest_ts = latest_bar.get("timestamp")

        close_prices = []
        for b in bars:
            val = getattr(b, "close", None)
            if val is None and isinstance(b, dict):
                val = b.get("close")
            if val is not None:
                close_prices.append(val)

        indicators: dict[str, float | None] = {}
        if len(close_prices) >= 50:
            try:
                indicators = calculate_indicators(close_prices)
            except Exception:
                indicators = {"rsi_14": None, "sma_20": None, "sma_50": None}
        else:
            indicators = {"rsi_14": None, "sma_20": None, "sma_50": None}

        market_context[sym] = {
            "latest_price": float(latest_price) if latest_price is not None else None,
            "timestamp": str(latest_ts) if latest_ts else None,
            "rsi_14": indicators.get("rsi_14"),
            "sma_20": indicators.get("sma_20"),
            "sma_50": indicators.get("sma_50"),
            "bar_count": len(bars),
        }

    # Sanitize account information (guarantee no credentials)
    acc = account_info or {}
    sanitized_account = {
        "cash": str(acc.get("cash", "100000.00")),
        "equity": str(acc.get("equity", "100000.00")),
        "buying_power": str(acc.get("buying_power", "400000.00")),
        "daily_pnl": str(acc.get("daily_pnl", "0.00")),
    }

    sanitized_positions = [
        {
            "symbol": pos.get("symbol"),
            "qty": pos.get("qty"),
            "market_value": str(pos.get("market_value")),
            "unrealized_pnl": str(pos.get("unrealized_pnl")),
        }
        for pos in (positions or [])
    ]

    sanitized_orders = [
        {
            "id": str(ord.get("id")),
            "symbol": ord.get("symbol"),
            "side": ord.get("side"),
            "qty": ord.get("qty"),
            "status": ord.get("status"),
        }
        for ord in (open_orders or [])
    ]

    return {
        "strategy_version": strategy_version,
        "watchlist": symbols,
        "market": market_context,
        "account": sanitized_account,
        "positions": sanitized_positions,
        "open_orders": sanitized_orders,
    }
