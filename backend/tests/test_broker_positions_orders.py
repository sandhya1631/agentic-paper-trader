"""Retrieve positions and open orders (Story 1.2.3 / Issue #60).

The actual retrieval logic (AlpacaClient.get_positions/get_orders and the
GET /broker/positions, /broker/orders endpoints) already existed — this file
is the missing test coverage proving the acceptance criteria: positions and
orders are retrieved and parsed correctly, empty results are handled without
error, and the protected endpoints require authentication.
"""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from httpx import AsyncClient

from app.broker.alpaca import AlpacaClient


def _mock_http_get(json_body):
    """Build a mocked httpx.AsyncClient whose GET returns the given JSON body."""
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = json_body
    http = AsyncMock(spec=httpx.AsyncClient)
    http.get = AsyncMock(return_value=response)
    http.is_closed = False  # otherwise AlpacaClient._get_client() discards this mock
    return http


# --- AlpacaClient.get_positions ---


@pytest.mark.asyncio
async def test_get_positions_parses_response():
    position = {
        "symbol": "AAPL",
        "qty": "10",
        "side": "long",
        "market_value": "1550.00",
        "cost_basis": "1500.00",
        "unrealized_pl": "50.00",
        "unrealized_plpc": "0.0333",
        "current_price": "155.00",
        "avg_entry_price": "150.00",
    }
    http = _mock_http_get([position])
    client = AlpacaClient(api_key="k", api_secret="s", client=http)

    positions = await client.get_positions()

    assert len(positions) == 1
    p = positions[0]
    assert p.symbol == "AAPL"
    assert p.qty == 10
    assert p.market_value == 1550
    assert p.unrealized_pl == 50
    assert p.current_price == 155
    assert p.avg_entry_price == 150


@pytest.mark.asyncio
async def test_get_positions_handles_empty_result():
    http = _mock_http_get([])
    client = AlpacaClient(api_key="k", api_secret="s", client=http)

    positions = await client.get_positions()

    assert positions == []


# --- AlpacaClient.get_orders ---


@pytest.mark.asyncio
async def test_get_orders_parses_response():
    order = {
        "id": "order-1",
        "client_order_id": "client-order-1",
        "symbol": "NVDA",
        "qty": "5",
        "filled_qty": "5",
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "status": "filled",
        "submitted_at": "2026-10-09T14:00:00Z",
        "filled_at": "2026-10-09T14:00:01Z",
    }
    http = _mock_http_get([order])
    client = AlpacaClient(api_key="k", api_secret="s", client=http)

    orders = await client.get_orders(status="all")

    assert len(orders) == 1
    o = orders[0]
    assert o.id == "order-1"
    assert o.symbol == "NVDA"
    assert o.qty == 5
    assert o.filled_qty == 5
    assert o.status == "filled"


@pytest.mark.asyncio
async def test_get_orders_handles_empty_result():
    http = _mock_http_get([])
    client = AlpacaClient(api_key="k", api_secret="s", client=http)

    orders = await client.get_orders(status="open")

    assert orders == []


@pytest.mark.asyncio
async def test_get_orders_forwards_status_filter():
    http = _mock_http_get([])
    client = AlpacaClient(api_key="k", api_secret="s", client=http)

    await client.get_orders(status="open")

    _, kwargs = http.get.call_args
    assert kwargs["params"]["status"] == "open"


@pytest.mark.asyncio
async def test_get_orders_defaults_missing_filled_qty_to_zero():
    order = {
        "id": "order-2",
        "client_order_id": "client-order-2",
        "symbol": "SPY",
        "qty": "3",
        "side": "sell",
        "type": "market",
        "time_in_force": "day",
        "status": "new",
    }
    http = _mock_http_get([order])
    client = AlpacaClient(api_key="k", api_secret="s", client=http)

    orders = await client.get_orders()

    assert orders[0].filled_qty == 0


# --- Router-level: protected routes require authentication ---


@pytest.mark.asyncio
async def test_get_positions_requires_auth(client: AsyncClient) -> None:
    response = await client.get("/api/v1/broker/positions")
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_get_orders_requires_auth(client: AsyncClient) -> None:
    response = await client.get("/api/v1/broker/orders")
    assert response.status_code in (401, 403)
