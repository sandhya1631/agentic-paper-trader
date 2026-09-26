import math

import pandas as pd


def calculate_indicators(prices):
    """Calculate RSI-14, SMA-20, and SMA-50."""

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

    return {
        "rsi_14": round(float(rsi), 2),
        "sma_20": round(float(sma_20), 2),
        "sma_50": round(float(sma_50), 2),
    }
