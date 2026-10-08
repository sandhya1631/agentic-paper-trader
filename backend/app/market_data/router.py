from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.dependencies import get_current_user
from app.db.models import User
from app.market_data.alpaca_client import (
    DEFAULT_BAR_LIMIT,
    DEFAULT_WATCHLIST,
    MarketDataCollector,
    OHLCVBar,
    create_market_data_collector,
)
from app.market_data.validation import MarketDataValidationError

router = APIRouter(prefix="/market-data", tags=["market-data"])


@router.get("/bars", response_model=dict[str, list[OHLCVBar]])
async def get_bars(
    symbols: list[str] = Query(default=DEFAULT_WATCHLIST),
    limit: int = Query(default=DEFAULT_BAR_LIMIT, ge=1, le=1000),
    lookback_days: int = Query(default=30, ge=1, le=90, description="Historical lookback window in days (default: 30 days)"),
    validate: bool = Query(default=False, description="Run market data quality validation checks"),
    _current_user: User = Depends(get_current_user),
) -> dict[str, list[OHLCVBar]]:
    """Latest OHLCV bars per symbol (default watchlist: AAPL, NVDA, SPY). Protected route."""
    try:
        collector: MarketDataCollector = create_market_data_collector()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    try:
        return await collector.fetch_latest_bars(
            symbols, limit=limit, lookback_days=lookback_days, validate=validate
        )
    except MarketDataValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"symbol": exc.symbol, "reason_code": exc.reason_code, "reason": exc.reason},
        ) from exc
