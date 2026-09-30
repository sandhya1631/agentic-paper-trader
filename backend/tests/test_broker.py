from decimal import Decimal
import pytest

from app.broker.alpaca import calculate_rsi, calculate_sma


def test_calculate_sma():
    prices = [Decimal(str(i)) for i in range(1, 26)]  # 1 to 25
    sma = calculate_sma(prices, period=20)
    assert sma is not None
    # Last 20 prices: 6 to 25 -> sum = 310 / 20 = 15.5
    assert sma == Decimal("15.5")


def test_calculate_sma_insufficient_data():
    prices = [Decimal("10.0"), Decimal("11.0")]
    sma = calculate_sma(prices, period=20)
    assert sma is None


def test_calculate_rsi():
    # 20 identical prices -> 0 change -> RSI = None or 100/50 depending on gains/losses
    prices = [Decimal("100.0")] * 20
    rsi = calculate_rsi(prices, period=14)
    assert rsi == Decimal("100")


def test_calculate_rsi_trending_up():
    # Increasing price series
    prices = [Decimal(str(100 + i)) for i in range(25)]
    rsi = calculate_rsi(prices, period=14)
    assert rsi is not None
    assert rsi > Decimal("70")
