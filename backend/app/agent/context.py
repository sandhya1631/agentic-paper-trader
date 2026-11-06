"""Agent Context Builder.

Compiles structured market data, technical indicators (RSI-14, SMA-20, SMA-50),
portfolio cash/equity, open positions, and open orders into a sanitized context
dictionary for the LLM. Strips all API keys, secrets, and credential tokens.
"""

from typing import Any

from app.core.constants import DEFAULT_STRATEGY_VERSION
from app.market_data.indicators import calculate_indicators


def _bar_value(bar: Any, field: str) -> Any:
    value = getattr(bar, field, None)
    if value is None and isinstance(bar, dict):
        value = bar.get(field)
    return value


def _recent_bar_summary(bars: list[Any], count: int = 12) -> list[dict[str, Any]]:
    """Return compact, model-friendly evidence for the most recent bars."""
    recent = bars[-count:]
    summary: list[dict[str, Any]] = []
    previous_close: float | None = None

    for bar in recent:
        close = _bar_value(bar, "close")
        if close is None:
            continue
        close_float = float(close)
        summary.append(
            {
                "timestamp": str(_bar_value(bar, "timestamp") or ""),
                "open": _bar_value(bar, "open"),
                "high": _bar_value(bar, "high"),
                "low": _bar_value(bar, "low"),
                "close": close_float,
                "volume": _bar_value(bar, "volume"),
                "change_pct": (
                    round((close_float / previous_close - 1) * 100, 4)
                    if previous_close not in (None, 0)
                    else None
                ),
            }
        )
        previous_close = close_float
    return summary


def build_agent_context(
    symbols: list[str],
    bars_per_symbol: dict[str, list[Any]],
    account_info: dict[str, Any] | None = None,
    positions: list[dict[str, Any]] | None = None,
    open_orders: list[dict[str, Any]] | None = None,
    strategy_version: str = DEFAULT_STRATEGY_VERSION,
    account_data_status: str = "NOT_PROVIDED",
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
        latest_price = _bar_value(latest_bar, "close")
        latest_ts = _bar_value(latest_bar, "timestamp")

        close_prices = []
        for b in bars:
            val = _bar_value(b, "close")
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
            "recent_bars": _recent_bar_summary(bars),
        }

    # Sanitize account information (guarantee no credentials)
    acc = account_info or {}
    account_defaults = (
        {
            "cash": "100000.00",
            "equity": "100000.00",
            "buying_power": "400000.00",
            "daily_pnl": "0.00",
        }
        if account_data_status == "NOT_PROVIDED"
        else {
            "cash": None,
            "equity": None,
            "buying_power": None,
            "daily_pnl": None,
        }
    )
    sanitized_account = {
        "cash": (
            str(acc["cash"]) if acc.get("cash") is not None else account_defaults["cash"]
        ),
        "equity": (
            str(acc["equity"])
            if acc.get("equity") is not None
            else account_defaults["equity"]
        ),
        "buying_power": (
            str(acc["buying_power"])
            if acc.get("buying_power") is not None
            else account_defaults["buying_power"]
        ),
        "daily_pnl": (
            str(acc["daily_pnl"])
            if acc.get("daily_pnl") is not None
            else account_defaults["daily_pnl"]
        ),
        "data_status": account_data_status,
    }

    sanitized_positions = [
        {
            "symbol": pos.get("symbol"),
            "qty": pos.get("qty"),
            "market_value": str(pos.get("market_value")),
            "unrealized_pnl": str(
                pos.get("unrealized_pnl", pos.get("unrealized_pl"))
            ),
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
