import math

import pandas as pd


def calculate_indicators(prices):
    """Calculate RSI-14, SMA-20, and SMA-50."""

    # We need at least 50 prices to calculate SMA-50
    if len(prices) < 50:
        raise ValueError("At least 50 price values are required.")

    # Check that every price is a valid number
    for price in prices:
        if price is None:
            raise ValueError("Price values cannot be None.")

        try:
            value = float(price)
        except (TypeError, ValueError):
            raise ValueError("All price values must be numbers.")

        if not math.isfinite(value):
            raise ValueError("All price values must be finite numbers.")

    prices = pd.Series(prices, dtype=float)

    # Calculate SMA-20 and SMA-50
    sma_20 = prices.rolling(window=20).mean().iloc[-1]
    sma_50 = prices.rolling(window=50).mean().iloc[-1]

    # Calculate RSI-14
    difference = prices.diff()
    gains = difference.clip(lower=0)
    losses = -difference.clip(upper=0)

    avg_gain = gains.rolling(window=14).mean().iloc[-1]
    avg_loss = losses.rolling(window=14).mean().iloc[-1]

    # Handle flat prices
    if avg_gain == 0 and avg_loss == 0:
        rsi = 50.0
    elif avg_loss == 0:
        rsi = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

    return {
        "rsi_14": round(float(rsi), 2),
        "sma_20": round(float(sma_20), 2),
        "sma_50": round(float(sma_50), 2),
    }
