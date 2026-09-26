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
    # Flat prices should not return NaN for RSI
    prices = [100] * 50

    result = calculate_indicators(prices)

    assert result["rsi_14"] == 50.0
