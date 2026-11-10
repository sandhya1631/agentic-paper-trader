import math

import pandas as pd


def calculate_indicators(prices, highs=None, lows=None, volumes=None):
    """Calculate technical indicators from validated OHLCV series."""

    if len(prices) < 50:
        raise ValueError("At least 50 price values are required.")

    validated_prices = []

    for price in prices:
        # Booleans should not be accepted as prices
        if isinstance(price, bool):
            raise ValueError("Price values must be numbers, not booleans.")

        if price is None:
            raise ValueError("Price values cannot be None.")

        try:
            value = float(price)
        except (TypeError, ValueError):
            raise ValueError("All price values must be numbers.")

        if not math.isfinite(value):
            raise ValueError("All price values must be finite numbers.")

        validated_prices.append(value)

    prices = pd.Series(validated_prices, dtype=float)

    def _optional_series(values, name):
        if values is None:
            return None
        if len(values) != len(prices):
            raise ValueError(f"{name} must have the same length as prices.")
        validated = []
        for value in values:
            if isinstance(value, bool) or value is None:
                raise ValueError(f"{name} values must be finite numbers.")
            try:
                converted = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"{name} values must be finite numbers.")
            if not math.isfinite(converted):
                raise ValueError(f"{name} values must be finite numbers.")
            validated.append(converted)
        return pd.Series(validated, dtype=float)

    highs_series = _optional_series(highs, "highs")
    lows_series = _optional_series(lows, "lows")
    volumes_series = _optional_series(volumes, "volumes")

    # SMA calculations
    sma_20 = prices.rolling(window=20).mean().iloc[-1]
    sma_50 = prices.rolling(window=50).mean().iloc[-1]

    # RSI-14 using Wilder's smoothing
    differences = prices.diff()

    gains = differences.clip(lower=0)
    losses = -differences.clip(upper=0)

    period = 14

    # Initial averages
    avg_gain = gains.iloc[1:period + 1].mean()
    avg_loss = losses.iloc[1:period + 1].mean()

    # Wilder's smoothing
    for i in range(period + 1, len(prices)):
        avg_gain = ((avg_gain * (period - 1)) + gains.iloc[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses.iloc[i]) / period

    # Handle special cases
    if avg_gain == 0 and avg_loss == 0:
        rsi = 50.0
    elif avg_loss == 0:
        rsi = 100.0
    elif avg_gain == 0:
        rsi = 0.0
    else:
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

    result = {
        "rsi_14": round(float(rsi), 2),
        "sma_20": round(float(sma_20), 2),
        "sma_50": round(float(sma_50), 2),
    }

    if highs_series is None and lows_series is None and volumes_series is None:
        return result

    sma_20_series = prices.rolling(window=20).mean()
    slope_index = len(prices) - 6
    sma_20_previous = sma_20_series.iloc[slope_index]
    result["sma_20_slope_pct"] = round(
        float((sma_20 - sma_20_previous) / sma_20_previous * 100)
        if sma_20_previous
        else 0.0,
        2,
    )

    ema_12 = prices.ewm(span=12, adjust=False).mean()
    ema_26 = prices.ewm(span=26, adjust=False).mean()
    macd_series = ema_12 - ema_26
    macd_signal = macd_series.ewm(span=9, adjust=False).mean().iloc[-1]
    result["macd"] = round(float(macd_series.iloc[-1]), 2)
    result["macd_signal"] = round(float(macd_signal), 2)
    result["macd_histogram"] = round(float(macd_series.iloc[-1] - macd_signal), 2)

    if highs_series is not None and lows_series is not None:
        previous_close = prices.shift(1)
        true_range = pd.concat(
            [
                highs_series - lows_series,
                (highs_series - previous_close).abs(),
                (lows_series - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        result["atr_14"] = round(float(true_range.iloc[-14:].mean()), 2)
    else:
        result["atr_14"] = None

    if volumes_series is not None:
        average_volume = volumes_series.iloc[-21:-1].mean()
        result["relative_volume_20"] = round(
            float(volumes_series.iloc[-1] / average_volume)
            if average_volume
            else 0.0,
            2,
        )
    else:
        result["relative_volume_20"] = None

    if sma_20 > sma_50 and result["sma_20_slope_pct"] > 0:
        regime = "UPTREND"
    elif sma_20 < sma_50 and result["sma_20_slope_pct"] < 0:
        regime = "DOWNTREND"
    else:
        regime = "RANGE"
    result["regime"] = regime
    return result
