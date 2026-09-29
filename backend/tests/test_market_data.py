import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.core.config import Settings
from app.market_data.alpaca_client import APIError as _APIError  # re-export check
from app.market_data.alpaca_client import (
    MarketDataCollector,
    create_market_data_collector,
)


def _make_bar(price: float) -> MagicMock:
    bar = MagicMock()
    bar.timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bar.open = price
    bar.high = price + 1
    bar.low = price - 1
    bar.close = price
    bar.volume = 1000
    return bar


def _make_api_error(status_code: int) -> Exception:
    from alpaca.common.exceptions import APIError

    error = APIError('{"code": 40010001, "message": "rate limited"}')
    error._http_error = MagicMock(response=MagicMock(status_code=status_code))
    return error


# --- MarketDataCollector ---


def test_collector_requires_credentials():
    with pytest.raises(ValueError):
        MarketDataCollector(api_key=None, api_secret=None)


@pytest.mark.asyncio
async def test_fetch_latest_bars_parses_response():
    collector = MarketDataCollector(api_key="fake-key", api_secret="fake-secret")

    fake_bar_set = MagicMock()
    fake_bar_set.data = {"AAPL": [_make_bar(150.0), _make_bar(151.0)]}

    with patch.object(collector._client, "get_stock_bars", return_value=fake_bar_set):
        result = await collector.fetch_latest_bars(["AAPL"], limit=2)

    assert list(result.keys()) == ["AAPL"]
    assert len(result["AAPL"]) == 2
    assert result["AAPL"][0].close == 150.0
    assert result["AAPL"][1].close == 151.0


@pytest.mark.asyncio
async def test_fetch_latest_bars_retries_on_rate_limit_then_succeeds():
    collector = MarketDataCollector(api_key="fake-key", api_secret="fake-secret")

    fake_bar_set = MagicMock()
    fake_bar_set.data = {"AAPL": [_make_bar(150.0)]}

    call_count = 0

    def flaky_get_stock_bars(request):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise _make_api_error(429)
        return fake_bar_set

    with patch.object(collector._client, "get_stock_bars", side_effect=flaky_get_stock_bars):
        with patch("app.market_data.alpaca_client.asyncio.sleep", new=AsyncMock()):
            result = await collector.fetch_latest_bars(["AAPL"])

    assert call_count == 2
    assert result["AAPL"][0].close == 150.0


@pytest.mark.asyncio
async def test_fetch_latest_bars_gives_up_after_max_retries():
    collector = MarketDataCollector(api_key="fake-key", api_secret="fake-secret")

    def always_rate_limited(request):
        raise _make_api_error(429)

    with patch.object(collector._client, "get_stock_bars", side_effect=always_rate_limited):
        with patch("app.market_data.alpaca_client.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(_APIError):
                await collector.fetch_latest_bars(["AAPL"])


@pytest.mark.asyncio
async def test_fetch_latest_bars_does_not_retry_non_rate_limit_errors():
    collector = MarketDataCollector(api_key="fake-key", api_secret="fake-secret")
    call_count = 0

    def failing_get_stock_bars(request):
        nonlocal call_count
        call_count += 1
        raise _make_api_error(500)

    with patch.object(collector._client, "get_stock_bars", side_effect=failing_get_stock_bars):
        with pytest.raises(_APIError):
            await collector.fetch_latest_bars(["AAPL"])

    assert call_count == 1


def test_create_market_data_collector_from_settings():
    settings = Settings(alpaca_api_key="fake-key", alpaca_api_secret="fake-secret")
    collector = create_market_data_collector(settings)
    assert isinstance(collector, MarketDataCollector)


# --- /market-data/bars endpoint ---


@pytest.mark.asyncio
async def test_market_data_bars_requires_auth(client: AsyncClient) -> None:
    response = await client.get("/market-data/bars")
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_market_data_bars_returns_data_when_authenticated(client: AsyncClient) -> None:
    email = f"test-{uuid.uuid4()}@example.com"
    password = "S3cure-Password!"
    await client.post("/auth/register", json={"email": email, "password": password})
    login = await client.post("/auth/login", json={"email": email, "password": password})
    token = login.json()["access_token"]

    fake_bar_set = MagicMock()
    fake_bar_set.data = {"AAPL": [_make_bar(150.0)], "NVDA": [], "SPY": []}

    with patch(
        "app.market_data.router.create_market_data_collector",
        return_value=MarketDataCollector(api_key="fake-key", api_secret="fake-secret"),
    ) as mock_factory:
        mock_factory.return_value._client.get_stock_bars = MagicMock(return_value=fake_bar_set)
        response = await client.get(
            "/market-data/bars", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["AAPL"][0]["close"] == 150.0
