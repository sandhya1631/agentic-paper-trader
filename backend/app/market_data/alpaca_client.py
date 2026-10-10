"""Market Data Collector — fetches OHLCV price bars from Alpaca's Data API.

Retries on rate limiting (HTTP 429) with exponential backoff, per story 1.1.1's
acceptance criteria. The underlying alpaca-py SDK is synchronous, so calls run
in a thread (`asyncio.to_thread`) to keep the FastAPI app non-blocking.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

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
        lookback_days: int = 30,
        validate: bool = False,
        min_bars: int | None = None,
    ) -> dict[str, list[OHLCVBar]]:
        """Return the latest `limit` bars per symbol (default watchlist: AAPL, NVDA, SPY)."""
        symbols = symbols or DEFAULT_WATCHLIST
        # A 30-day lookback window ensures enough 5-minute bars are returned across weekends,
        # holidays, and non-trading hours to fulfill the requested `limit`.
        start_time = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        result: dict[str, list[OHLCVBar]] = {symbol: [] for symbol in symbols}
        effective_min_bars = min_bars if min_bars is not None else limit

        async def _fetch_single_symbol(sym: str) -> tuple[str, list[OHLCVBar]]:
            req = StockBarsRequest(
                symbol_or_symbols=sym,
                timeframe=TimeFrame(timeframe_minutes, TimeFrameUnit.Minute),
                limit=limit,
                start=start_time,
                feed=DataFeed.IEX,
            )
            # Deliberately not caught here: a fetch/retry failure (APIError) or a
            # data-quality failure (MarketDataValidationError) must propagate, per the
            # architecture doc's failure-handling spec ("fail cycle if freshness cannot
            # be met"). The caller (run_agent_cycle) already catches broadly and falls
            # back safely; swallowing it here instead would silently hide the failure
            # as an empty bar list indistinguishable from "symbol legitimately has no
            # bars," and would make /market-data/bars?validate=true never actually
            # return its documented 422 on bad data.
            bar_set = await self._get_bars_with_retry(req)
            bars = bar_set.data.get(sym, [])
            parsed = [
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
                parsed = validate_ohlcv_bars(sym, parsed, min_bars=effective_min_bars)
            return sym, parsed

        tasks = [_fetch_single_symbol(s) for s in symbols]
        fetched_tuples = await asyncio.gather(*tasks)
        for sym, parsed_bars in fetched_tuples:
            result[sym] = parsed_bars

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
