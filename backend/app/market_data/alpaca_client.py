"""Market Data Collector — fetches OHLCV price bars from Alpaca's Data API.

Retries on rate limiting (HTTP 429) with exponential backoff, per story 1.1.1's
acceptance criteria. The underlying alpaca-py SDK is synchronous, so calls run
in a thread (`asyncio.to_thread`) to keep the FastAPI app non-blocking.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import logging

from alpaca.common.exceptions import APIError
from alpaca.data.enums import DataFeed
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from pydantic import BaseModel

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

DEFAULT_WATCHLIST = ["AAPL", "NVDA", "SPY"]
DEFAULT_BAR_LIMIT = 50
DEFAULT_TIMEFRAME_MINUTES = 5
MAX_RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 1.0

RATE_LIMIT_STATUS = 429


class OHLCVBar(BaseModel):
    """A single OHLCV candlestick bar."""

    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float


def _is_rate_limited(error: APIError) -> bool:
    return error.status_code == RATE_LIMIT_STATUS


class MarketDataCollector:
    """Fetches the latest OHLCV bars for a symbol watchlist via Alpaca's Data API."""

    def __init__(self, api_key: str | None, api_secret: str | None) -> None:
        if not api_key or not api_secret:
            raise ValueError(
                "ALPACA_API_KEY and ALPACA_API_SECRET are required to fetch market data"
            )
        self._client = StockHistoricalDataClient(api_key=api_key, secret_key=api_secret)

    async def fetch_latest_bars(
        self,
        symbols: list[str] | None = None,
        *,
        limit: int = DEFAULT_BAR_LIMIT,
        timeframe_minutes: int = DEFAULT_TIMEFRAME_MINUTES,
        validate: bool = False,
    ) -> dict[str, list[OHLCVBar]]:
        """Return the latest `limit` bars per symbol (default watchlist: AAPL, NVDA, SPY)."""
        symbols = symbols or DEFAULT_WATCHLIST
        start_time = datetime.now(timezone.utc) - timedelta(days=7)
        result: dict[str, list[OHLCVBar]] = {symbol: [] for symbol in symbols}

        for symbol in symbols:
            request = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame(timeframe_minutes, TimeFrameUnit.Minute),
                limit=limit,
                start=start_time,
                feed=DataFeed.IEX,
            )
            bar_set = await self._get_bars_with_retry(request)
            bars = bar_set.data.get(symbol, [])
            parsed_bars = [
                OHLCVBar(
                    timestamp=bar.timestamp.isoformat(),
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                )
                for bar in bars
            ]
            if validate:
                from app.market_data.validation import validate_ohlcv_bars
                parsed_bars = validate_ohlcv_bars(symbol, parsed_bars, min_bars=0)
            result[symbol] = parsed_bars
        return result

    async def _get_bars_with_retry(self, request: StockBarsRequest):
        attempt = 0
        while True:
            attempt += 1
            try:
                return await asyncio.to_thread(self._client.get_stock_bars, request)
            except APIError as exc:
                if _is_rate_limited(exc) and attempt < MAX_RETRY_ATTEMPTS:
                    backoff = RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
                    logger.warning(
                        "Alpaca rate limit hit (attempt %s/%s); retrying in %.1fs",
                        attempt,
                        MAX_RETRY_ATTEMPTS,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                    continue
                raise


def create_market_data_collector(settings: Settings | None = None) -> MarketDataCollector:
    """Market Data Collector factory, reading Alpaca credentials from settings."""
    settings = settings or get_settings()
    return MarketDataCollector(settings.alpaca_api_key, settings.alpaca_api_secret)
