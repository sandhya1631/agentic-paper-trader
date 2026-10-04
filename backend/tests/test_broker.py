from app.market_data.indicators import calculate_indicators


def test_calculate_indicators_sma():
    prices = [float(i) for i in range(1, 60)]  # 1 to 59
    indicators = calculate_indicators(prices)
    assert indicators["sma_20"] == 49.5  # average of 40 to 59
    assert indicators["sma_50"] == 34.5  # average of 10 to 59


def test_calculate_indicators_flat_prices():
    prices = [100.0] * 55
    indicators = calculate_indicators(prices)
    assert indicators["rsi_14"] == 50.0  # neutral RSI for flat stock
    assert indicators["sma_20"] == 100.0
    assert indicators["sma_50"] == 100.0


def test_calculate_indicators_trending_up():
    prices = [float(100 + i) for i in range(55)]
    indicators = calculate_indicators(prices)
    assert indicators["rsi_14"] > 70.0  # overbought for steady upward trend


