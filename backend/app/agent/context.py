"""Agent Context Builder.

Compiles structured market data, technical indicators (RSI-14, SMA-20, SMA-50),
portfolio cash/equity, open positions, open orders and market status into a
sanitized context dictionary for the LLM. Strips all API keys, secrets, and
credential tokens.

When the live account could not be read (``account_info`` is None) the context says
so (``account_data: "UNAVAILABLE"``) instead of inventing balances: fabricated cash
or "no positions" would mislead the model, so the prompt tells it to HOLD.
"""

from datetime import datetime, timezone
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


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def _bar_quality(
    bars: list[Any], latest_timestamp: Any, max_age_seconds: int
) -> dict[str, Any]:
    timestamps = [_bar_value(bar, "timestamp") for bar in bars]
    timestamp_keys = [str(timestamp) for timestamp in timestamps if timestamp is not None]
    duplicate_count = len(timestamp_keys) - len(set(timestamp_keys))
    missing_close_count = sum(_bar_value(bar, "close") is None for bar in bars)
    parsed_latest = _parse_timestamp(latest_timestamp)

    if parsed_latest is None:
        freshness_status = "UNKNOWN"
        age_seconds = None
    else:
        age_seconds = max(
            0, int((datetime.now(timezone.utc) - parsed_latest).total_seconds())
        )
        freshness_status = "FRESH" if age_seconds <= max_age_seconds else "STALE"

    quality_status = (
        "VALID"
        if missing_close_count == 0 and duplicate_count == 0
        else "DEGRADED"
    )
    return {
        "age_seconds": age_seconds,
        "freshness_status": freshness_status,
        "missing_close_count": missing_close_count,
        "duplicate_timestamp_count": duplicate_count,
        "quality_status": quality_status,
    }


def build_agent_context(
    symbols: list[str],
    bars_per_symbol: dict[str, list[Any]],
    account_info: dict[str, Any] | None = None,
    positions: list[dict[str, Any]] | None = None,
    open_orders: list[dict[str, Any]] | None = None,
    strategy_version: str = DEFAULT_STRATEGY_VERSION,
    account_data_status: str | None = None,
    market_open: bool | None = None,
    max_data_age_seconds: int = 900,
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
                "data_quality": {
                    "age_seconds": None,
                    "freshness_status": "UNKNOWN",
                    "missing_close_count": 0,
                    "duplicate_timestamp_count": 0,
                    "quality_status": "NO_DATA",
                },
            }
            continue

        latest_bar = bars[-1]
        latest_price = _bar_value(latest_bar, "close")
        latest_ts = _bar_value(latest_bar, "timestamp")

        close_prices = []
        high_prices = []
        low_prices = []
        volumes = []
        for b in bars:
            val = _bar_value(b, "close")
            if val is not None:
                close_prices.append(val)
            high_prices.append(_bar_value(b, "high"))
            low_prices.append(_bar_value(b, "low"))
            volumes.append(_bar_value(b, "volume"))

        indicators: dict[str, float | None] = {}
        if len(close_prices) >= 50:
            try:
                indicators = calculate_indicators(
                    close_prices,
                    highs=high_prices if all(value is not None for value in high_prices) else None,
                    lows=low_prices if all(value is not None for value in low_prices) else None,
                    volumes=volumes if all(value is not None for value in volumes) else None,
                )
            except Exception:
                indicators = {}
        else:
            indicators = {}

        market_context[sym] = {
            "latest_price": float(latest_price) if latest_price is not None else None,
            "timestamp": str(latest_ts) if latest_ts else None,
            "rsi_14": indicators.get("rsi_14"),
            "sma_20": indicators.get("sma_20"),
            "sma_50": indicators.get("sma_50"),
            "sma_20_slope_pct": indicators.get("sma_20_slope_pct"),
            "macd": indicators.get("macd"),
            "macd_signal": indicators.get("macd_signal"),
            "macd_histogram": indicators.get("macd_histogram"),
            "atr_14": indicators.get("atr_14"),
            "relative_volume_20": indicators.get("relative_volume_20"),
            "regime": indicators.get("regime"),
            "bar_count": len(bars),
            "recent_bars": _recent_bar_summary(bars),
            "data_quality": _bar_quality(bars, latest_ts, max_data_age_seconds),
        }

    # Sanitize account information (guarantee no credentials). Never invent numbers:
    # without live data the account is marked UNAVAILABLE.
    #
    # Two call conventions coexist here (#64/#172 reconciled with #174):
    #   - account_data_status omitted (None): the original, simpler convention - LIVE/
    #     UNAVAILABLE is inferred from whether account_info itself was given, and an
    #     unavailable account is the minimal {"status": "UNAVAILABLE"} (no invented
    #     values, not even None ones). This is what the real orchestrator pipeline's
    #     callers that predate #174 - and most direct tests - use.
    #   - account_data_status given explicitly: #174's richer convention - a granular
    #     reason (e.g. "UNAVAILABLE_BROKER") is kept alongside cash/equity/etc. (real
    #     value or null), so observability can show why. "NOT_PROVIDED" specifically
    #     is the one case that still fills in placeholder numbers, for a caller that
    #     explicitly says it never looked - never the default.
    acc = account_info or {}
    account_available = account_info is not None
    if account_data_status is None:
        if account_available:
            sanitized_account: dict[str, Any] = {
                key: (str(acc[key]) if acc.get(key) is not None else None)
                for key in ("cash", "equity", "buying_power", "daily_pnl")
            }
        else:
            sanitized_account = {"status": "UNAVAILABLE"}
        account_data = "LIVE" if account_available else "UNAVAILABLE"
    else:
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
        # Flat, binary signal for the prompt's "is live account data available" rule
        # (#64/#172): whatever granular account_data_status a caller passes, this always
        # collapses to LIVE/UNAVAILABLE, so the prompt text never needs the specific reason.
        account_data = "LIVE" if account_data_status == "LIVE" else "UNAVAILABLE"

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

    sanitized_orders: list[dict[str, Any]] = []
    for ord in open_orders or []:
        sanitized_order: dict[str, Any] = {
            "id": str(ord.get("id")),
            "symbol": ord.get("symbol"),
            "side": ord.get("side"),
            "qty": ord.get("qty"),
            "status": ord.get("status"),
        }
        # A notional (dollar-amount) order has no qty; show its amount instead (#177).
        if ord.get("notional") is not None:
            sanitized_order["notional"] = str(ord.get("notional"))
        sanitized_orders.append(sanitized_order)

    # Tell the model whether the market is open, closed or unknown.
    if market_open is None:
        market_status = "UNKNOWN"
    else:
        market_status = "OPEN" if market_open else "CLOSED"

    return {
        "strategy_version": strategy_version,
        "watchlist": symbols,
        "market_status": market_status,
        "market": market_context,
        "account_data": account_data,
        "account": sanitized_account,
        "positions": sanitized_positions,
        "open_orders": sanitized_orders,
    }