import math

import pandas as pd
import pandas_ta as ta


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

    rsi = ta.rsi(prices, length=14)
    sma_20 = ta.sma(prices, length=20)
    sma_50 = ta.sma(prices, length=50)

    rsi_value = rsi.iloc[-1]

    # RSI may be NaN when all prices are the same
    if pd.isna(rsi_value):
        rsi_value = 50.0

    results = {
        "rsi_14": round(float(rsi_value), 2),
        "sma_20": round(float(sma_20.iloc[-1]), 2),
        "sma_50": round(float(sma_50.iloc[-1]), 2),
    }

    return results
