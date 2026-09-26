import pandas as pd
import pandas_ta as ta


def calculate_indicators(prices):
    """Calculate RSI-14, SMA-20, and SMA-50."""

    prices = pd.Series(prices, dtype=float)

    # We need at least 50 prices to calculate SMA-50
    if len(prices) < 50:
        raise ValueError("At least 50 price values are required.")

    rsi = ta.rsi(prices, length=14)
    sma_20 = ta.sma(prices, length=20)
    sma_50 = ta.sma(prices, length=50)

    results = {
        "rsi_14": round(float(rsi.iloc[-1]), 2),
        "sma_20": round(float(sma_20.iloc[-1]), 2),
        "sma_50": round(float(sma_50.iloc[-1]), 2),
    }

    return results
