import pytest

from app.market_data.indicators import calculate_indicators


def test_calculate_indicators():
    prices = list(range(100, 160))

    result = calculate_indicators(prices)

    assert "rsi_14" in result
    assert "sma_20" in result
    assert "sma_50" in result

    assert isinstance(result["rsi_14"], float)
    assert isinstance(result["sma_20"], float)
    assert isinstance(result["sma_50"], float)


def test_indicator_rounding():
    prices = list(range(100, 160))

    result = calculate_indicators(prices)

    for value in result.values():
        assert value == round(value, 2)


def test_less_than_50_prices():
    # 49 prices should be rejected
    prices = list(range(100, 149))

    with pytest.raises(ValueError):
        calculate_indicators(prices)


def test_exactly_50_prices():
    # Exactly 50 prices should work
    prices = list(range(100, 150))

    result = calculate_indicators(prices)

    assert "rsi_14" in result
    assert "sma_20" in result
    assert "sma_50" in result


def test_none_price_rejected():
    prices = list(range(100, 150))
    prices[10] = None

    with pytest.raises(ValueError):
        calculate_indicators(prices)


def test_flat_prices():
    # Flat prices should return a neutral RSI
    prices = [100] * 50

    result = calculate_indicators(prices)

    assert result["rsi_14"] == 50.0


def test_nan_price_rejected():
    prices = list(range(100, 150))
    prices[10] = float("nan")

    with pytest.raises(ValueError):
        calculate_indicators(prices)


def test_boolean_price_rejected():
    prices = list(range(100, 150))
    prices[10] = True

    with pytest.raises(ValueError):
        calculate_indicators(prices)


def test_rsi_wilder_calculation():
    # Known price data used to verify Wilder's RSI calculation
    prices = [
        44.34, 44.09, 44.15, 43.61, 44.33,
        44.83, 45.10, 45.42, 45.84, 46.08,
        45.89, 46.03, 45.61, 46.28, 46.28,
        46.00, 46.03, 46.41, 46.22, 45.64,
        46.21, 46.25, 45.71, 46.45, 45.78,
        45.35, 44.03, 44.18, 44.22, 44.57,
        43.42, 42.66, 43.13, 42.74, 42.75,
        42.96, 42.21, 43.13, 42.79, 42.57,
        42.39, 42.19, 42.22, 42.63, 42.04,
        41.69, 41.83, 42.22, 42.53, 42.39,
    ]

    result = calculate_indicators(prices)

    # Expected RSI-14 using Wilder's smoothing
    assert result["rsi_14"] == pytest.approx(43.43, abs=0.01)
