from decimal import Decimal
from typing import Any
from pydantic import BaseModel, ConfigDict, Field

from app.db.enums import OrderSide, OrderType, TimeInForce


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AlpacaAccountRead(APIModel):
    id: str
    status: str
    currency: str = "USD"
    cash: Decimal
    portfolio_value: Decimal
    buying_power: Decimal
    equity: Decimal
    long_market_value: Decimal = Decimal("0")
    short_market_value: Decimal = Decimal("0")
    initial_margin: Decimal = Decimal("0")
    maintenance_margin: Decimal = Decimal("0")
    daytrading_buying_power: Decimal = Decimal("0")
    regt_buying_power: Decimal = Decimal("0")
    is_paper: bool = True


class AlpacaPositionRead(APIModel):
    symbol: str
    qty: Decimal
    side: str
    market_value: Decimal
    cost_basis: Decimal
    unrealized_pl: Decimal
    unrealized_plpc: Decimal
    current_price: Decimal
    avg_entry_price: Decimal


class MarketSnapshotRead(APIModel):
    symbol: str
    latest_close: Decimal
    rsi_14: Decimal | None = None
    sma_20: Decimal | None = None
    bars_count: int
    timestamp: str | None = None


class OrderCreate(APIModel):
    symbol: str
    qty: int = Field(gt=0, description="Quantity of shares to buy/sell")
    side: OrderSide
    type: OrderType = OrderType.MARKET
    time_in_force: TimeInForce = TimeInForce.DAY
    limit_price: Decimal | None = Field(default=None, gt=0)


class AlpacaOrderRead(APIModel):
    id: str
    client_order_id: str
    symbol: str
    qty: Decimal
    filled_qty: Decimal = Decimal("0")
    side: str
    type: str
    time_in_force: str
    status: str
    submitted_at: str | None = None
    filled_at: str | None = None
