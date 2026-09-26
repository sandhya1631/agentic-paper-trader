from app.market_data.indicators import calculate_indicators


def test_calculate_indicators():
    # Create simple sample price data
    prices = list(range(100, 160))

    result = calculate_indicators(prices)

    # Check that all indicators are returned
    assert "rsi_14" in result
    assert "sma_20" in result
    assert "sma_50" in result

    # Check that results are numbers
    assert isinstance(result["rsi_14"], float)
    assert isinstance(result["sma_20"], float)
    assert isinstance(result["sma_50"], float)


def test_indicator_rounding():
    prices = list(range(100, 160))

    result = calculate_indicators(prices)

    # Values should be rounded to 2 decimal places
    for value in result.values():
        assert value == round(value, 2)


def test_not_enough_prices():
    prices = [100, 101, 102]

    try:
        calculate_indicators(prices)
        assert False
    except ValueError:
        assert True
