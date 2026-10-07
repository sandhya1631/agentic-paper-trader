"""Unit tests for Market Data Quality Validation (Story 1.1.2 / Issue #58)."""

from datetime import datetime, timedelta, timezone

import pytest

from app.market_data.alpaca_client import OHLCVBar
from app.market_data.validation import (
    MarketDataValidationError,
    ValidationErrorReason,
    validate_ohlcv_bars,
)


def _generate_valid_bars(count: int = 50) -> list[OHLCVBar]:
    base_time = datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc)
    bars = []
    for i in range(count):
        ts = (base_time + timedelta(minutes=5 * i)).isoformat()
        bars.append(
            OHLCVBar(
                timestamp=ts,
                open=150.0 + i * 0.1,
                high=152.0 + i * 0.1,
                low=149.0 + i * 0.1,
                close=151.0 + i * 0.1,
                volume=1000.0 + i * 10,
            )
        )
    return bars


def test_validate_valid_bars():
    bars = _generate_valid_bars(50)
    result = validate_ohlcv_bars("AAPL", bars, min_bars=50)
    assert len(result) == 50
    assert result[0].timestamp == bars[0].timestamp


def test_validate_empty_bars():
    with pytest.raises(MarketDataValidationError) as exc_info:
        validate_ohlcv_bars("AAPL", [], min_bars=50)

    err = exc_info.value
    assert err.symbol == "AAPL"
    assert err.reason_code == ValidationErrorReason.EMPTY_DATA
    assert "No candlestick bars provided" in err.reason


def test_validate_insufficient_bars():
    bars = _generate_valid_bars(10)
    with pytest.raises(MarketDataValidationError) as exc_info:
        validate_ohlcv_bars("AAPL", bars, min_bars=50)

    err = exc_info.value
    assert err.reason_code == ValidationErrorReason.INSUFFICIENT_BARS
    assert "at least 50 are required" in err.reason


def test_validate_duplicate_timestamp():
    bars = _generate_valid_bars(50)
    # Inject duplicate timestamp at index 5
    bars[5] = OHLCVBar(
        timestamp=bars[4].timestamp,
        open=150.0,
        high=152.0,
        low=149.0,
        close=151.0,
        volume=1000.0,
    )

    with pytest.raises(MarketDataValidationError) as exc_info:
        validate_ohlcv_bars("AAPL", bars, min_bars=50)

    err = exc_info.value
    assert err.reason_code == ValidationErrorReason.DUPLICATE_TIMESTAMP
    assert "Duplicate timestamp detected" in err.reason


def test_validate_out_of_order_timestamps():
    bars = _generate_valid_bars(50)
    # Swap timestamps at index 10 and 11
    bars[10], bars[11] = bars[11], bars[10]

    with pytest.raises(MarketDataValidationError) as exc_info:
        validate_ohlcv_bars("AAPL", bars, min_bars=50)

    err = exc_info.value
    assert err.reason_code == ValidationErrorReason.OUT_OF_ORDER_TIMESTAMPS
    assert "Out-of-order timestamp" in err.reason


def test_validate_negative_price():
    bars = _generate_valid_bars(50)
    bars[20] = OHLCVBar(
        timestamp=bars[20].timestamp,
        open=150.0,
        high=152.0,
        low=149.0,
        close=-5.0,  # Negative close price
        volume=1000.0,
    )

    with pytest.raises(MarketDataValidationError) as exc_info:
        validate_ohlcv_bars("AAPL", bars, min_bars=50)

    err = exc_info.value
    assert err.reason_code == ValidationErrorReason.INVALID_PRICE
    assert "non-positive price" in err.reason


def test_validate_invalid_high_low_bounds():
    bars = _generate_valid_bars(50)
    # High is lower than open/close
    bars[15] = OHLCVBar(
        timestamp=bars[15].timestamp,
        open=150.0,
        high=140.0,  # High < Open
        low=135.0,
        close=148.0,
        volume=1000.0,
    )

    with pytest.raises(MarketDataValidationError) as exc_info:
        validate_ohlcv_bars("AAPL", bars, min_bars=50)

    err = exc_info.value
    assert err.reason_code == ValidationErrorReason.INVALID_PRICE_BOUNDS
    assert "invalid 'high'" in err.reason


def test_validate_negative_volume():
    bars = _generate_valid_bars(50)
    bars[30] = OHLCVBar(
        timestamp=bars[30].timestamp,
        open=150.0,
        high=152.0,
        low=149.0,
        close=151.0,
        volume=-100.0,  # Negative volume
    )

    with pytest.raises(MarketDataValidationError) as exc_info:
        validate_ohlcv_bars("AAPL", bars, min_bars=50)

    err = exc_info.value
    assert err.reason_code == ValidationErrorReason.NEGATIVE_VOLUME
    assert "negative volume" in err.reason


def test_validate_nan_or_inf():
    bars = _generate_valid_bars(50)
    bars[5] = OHLCVBar(
        timestamp=bars[5].timestamp,
        open=float("nan"),
        high=152.0,
        low=149.0,
        close=151.0,
        volume=1000.0,
    )

    with pytest.raises(MarketDataValidationError) as exc_info:
        validate_ohlcv_bars("AAPL", bars, min_bars=50)

    err = exc_info.value
    assert err.reason_code == ValidationErrorReason.NON_FINITE_VALUE
    assert "non-finite value" in err.reason


def test_validate_dict_bars():
    valid_bars = _generate_valid_bars(50)
    dict_bars = [
        {
            "timestamp": bar.timestamp,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        }
        for bar in valid_bars
    ]

    result = validate_ohlcv_bars("NVDA", dict_bars, min_bars=50)
    assert len(result) == 50
    assert isinstance(result[0], OHLCVBar)
    assert result[0].close == valid_bars[0].close
