"""Market Data Quality Validator

Detects missing, duplicate, out-of-order, or invalid OHLCV bars data to ensure
corrupted market data does not proceed to technical indicator calculation, LLM
decision making, or order execution.
"""

import math
from datetime import datetime
from enum import Enum
from typing import Any

from app.market_data.alpaca_client import OHLCVBar


class ValidationErrorReason(str, Enum):
    """Specific reason codes for market data validation failures."""

    EMPTY_DATA = "EMPTY_DATA"
    INSUFFICIENT_BARS = "INSUFFICIENT_BARS"
    DUPLICATE_TIMESTAMP = "DUPLICATE_TIMESTAMP"
    OUT_OF_ORDER_TIMESTAMPS = "OUT_OF_ORDER_TIMESTAMPS"
    INVALID_PRICE = "INVALID_PRICE"
    INVALID_PRICE_BOUNDS = "INVALID_PRICE_BOUNDS"
    NEGATIVE_VOLUME = "NEGATIVE_VOLUME"
    NON_FINITE_VALUE = "NON_FINITE_VALUE"


class MarketDataValidationError(Exception):
    """Raised when market data fails quality validation."""

    def __init__(
        self,
        symbol: str,
        reason_code: ValidationErrorReason,
        reason: str,
    ) -> None:
        self.symbol = symbol.upper() if symbol else "UNKNOWN"
        self.reason_code = reason_code
        self.reason = reason
        super().__init__(f"[{self.symbol}] Market data validation failed ({reason_code.value}): {reason}")


def _parse_timestamp(ts: Any) -> datetime:
    if isinstance(ts, datetime):
        return ts
    if isinstance(ts, str):
        # Handle ISO formatted strings
        clean_ts = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(clean_ts)
    raise ValueError(f"Invalid timestamp format: {ts}")


def validate_ohlcv_bars(
    symbol: str,
    bars: list[OHLCVBar | dict[str, Any]],
    *,
    min_bars: int = 50,
) -> list[OHLCVBar]:
    """Validate a sequence of OHLCV candlestick bars for a given symbol.

    Performs the following quality checks:
      1. Non-empty & minimum bar count check.
      2. Non-finite value check (NaN / Inf for prices and volume).
      3. Strict positivity check for prices (open, high, low, close > 0).
      4. OHLC logical bound check (high >= max(open, close, low), low <= min(open, close, high)).
      5. Volume non-negativity check (volume >= 0).
      6. Timestamp uniqueness & strictly ascending sequence order check.

    Raises `MarketDataValidationError` on any violation.
    Returns normalized list of `OHLCVBar` instances.
    """
    sym = symbol.upper() if symbol else "UNKNOWN"

    # 1. Empty Check
    if not bars:
        raise MarketDataValidationError(
            symbol=sym,
            reason_code=ValidationErrorReason.EMPTY_DATA,
            reason="No candlestick bars provided.",
        )

    # Minimum Bar Count Check
    if min_bars > 0 and len(bars) < min_bars:
        raise MarketDataValidationError(
            symbol=sym,
            reason_code=ValidationErrorReason.INSUFFICIENT_BARS,
            reason=f"Received {len(bars)} bars, but at least {min_bars} are required.",
        )

    normalized_bars: list[OHLCVBar] = []
    seen_timestamps: set[datetime] = set()
    prev_timestamp: datetime | None = None

    for idx, bar in enumerate(bars):
        # Convert dict to OHLCVBar if needed
        if isinstance(bar, dict):
            try:
                bar_obj = OHLCVBar(
                    timestamp=str(bar["timestamp"]),
                    open=float(bar["open"]),
                    high=float(bar["high"]),
                    low=float(bar["low"]),
                    close=float(bar["close"]),
                    volume=float(bar["volume"]),
                )
            except (KeyError, ValueError, TypeError) as exc:
                raise MarketDataValidationError(
                    symbol=sym,
                    reason_code=ValidationErrorReason.NON_FINITE_VALUE,
                    reason=f"Bar at index {idx} has missing or malformed numeric fields: {exc}",
                ) from exc
        else:
            bar_obj = bar

        # 2. Non-Finite Value Check
        values_to_check = [
            ("open", bar_obj.open),
            ("high", bar_obj.high),
            ("low", bar_obj.low),
            ("close", bar_obj.close),
            ("volume", bar_obj.volume),
        ]
        for name, val in values_to_check:
            if not math.isfinite(val):
                raise MarketDataValidationError(
                    symbol=sym,
                    reason_code=ValidationErrorReason.NON_FINITE_VALUE,
                    reason=f"Bar at index {idx} has non-finite value for '{name}': {val}",
                )

        # 3. Positivity Check for Prices
        prices_to_check = [
            ("open", bar_obj.open),
            ("high", bar_obj.high),
            ("low", bar_obj.low),
            ("close", bar_obj.close),
        ]
        for name, val in prices_to_check:
            if val <= 0:
                raise MarketDataValidationError(
                    symbol=sym,
                    reason_code=ValidationErrorReason.INVALID_PRICE,
                    reason=f"Bar at index {idx} has non-positive price for '{name}': {val}",
                )

        # 4. Logical Bounds Check
        max_open_close = max(bar_obj.open, bar_obj.close)
        min_open_close = min(bar_obj.open, bar_obj.close)

        if bar_obj.high < max_open_close or bar_obj.high < bar_obj.low:
            raise MarketDataValidationError(
                symbol=sym,
                reason_code=ValidationErrorReason.INVALID_PRICE_BOUNDS,
                reason=(
                    f"Bar at index {idx} has invalid 'high' ({bar_obj.high}) "
                    f"relative to open ({bar_obj.open}), close ({bar_obj.close}), or low ({bar_obj.low})."
                ),
            )

        if bar_obj.low > min_open_close or bar_obj.low > bar_obj.high:
            raise MarketDataValidationError(
                symbol=sym,
                reason_code=ValidationErrorReason.INVALID_PRICE_BOUNDS,
                reason=(
                    f"Bar at index {idx} has invalid 'low' ({bar_obj.low}) "
                    f"relative to open ({bar_obj.open}), close ({bar_obj.close}), or high ({bar_obj.high})."
                ),
            )

        # 5. Non-Negative Volume Check
        if bar_obj.volume < 0:
            raise MarketDataValidationError(
                symbol=sym,
                reason_code=ValidationErrorReason.NEGATIVE_VOLUME,
                reason=f"Bar at index {idx} has negative volume: {bar_obj.volume}",
            )

        # 6. Timestamp Sequence & Duplicate Check
        try:
            ts_dt = _parse_timestamp(bar_obj.timestamp)
        except ValueError as exc:
            raise MarketDataValidationError(
                symbol=sym,
                reason_code=ValidationErrorReason.OUT_OF_ORDER_TIMESTAMPS,
                reason=f"Bar at index {idx} has invalid timestamp format: {bar_obj.timestamp}",
            ) from exc

        if ts_dt in seen_timestamps:
            raise MarketDataValidationError(
                symbol=sym,
                reason_code=ValidationErrorReason.DUPLICATE_TIMESTAMP,
                reason=f"Duplicate timestamp detected at index {idx}: '{bar_obj.timestamp}'.",
            )

        if prev_timestamp is not None and ts_dt <= prev_timestamp:
            raise MarketDataValidationError(
                symbol=sym,
                reason_code=ValidationErrorReason.OUT_OF_ORDER_TIMESTAMPS,
                reason=(
                    f"Out-of-order timestamp at index {idx} ('{bar_obj.timestamp}' "
                    f"<= previous timestamp '{prev_timestamp.isoformat()}')."
                ),
            )

        seen_timestamps.add(ts_dt)
        prev_timestamp = ts_dt
        normalized_bars.append(bar_obj)

    return normalized_bars
