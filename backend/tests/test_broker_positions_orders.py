"""Retrieve positions and open orders (Story 1.2.3 / Issue #60).

The actual retrieval logic (AlpacaClient.get_positions/get_orders and the
GET /broker/positions, /broker/orders endpoints) already existed — this file
is the missing test coverage proving the acceptance criteria: positions and
orders are retrieved and parsed correctly, empty results are handled without
error, and the protected endpoints require authentication.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from alpaca_order_book import NOTIONAL_ORDER_JSON, FakeOrderBook, make_order
from fastapi import HTTPException
from httpx import AsyncClient

from app.broker import alpaca as alpaca_module
from app.broker.alpaca import AlpacaClient, fetch_all_orders
from app.broker.router import get_alpaca_client, place_paper_order
from app.broker.schemas import AlpacaOrderRead, OrderCreate
from app.core.constants import ALPACA_ORDERS_PAGE_LIMIT
from app.main import app


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


# --- Notional orders: Alpaca returns qty null (#177) ---


@pytest.mark.asyncio
async def test_get_orders_parses_a_notional_order_with_null_qty():
    http = _mock_http_get([NOTIONAL_ORDER_JSON])
    client = AlpacaClient(api_key="k", api_secret="s", client=http)

    orders = await client.get_orders(status="all")

    assert len(orders) == 1
    assert orders[0].qty is None
    assert orders[0].notional == Decimal("100")
    assert orders[0].filled_qty == 0


@pytest.mark.asyncio
async def test_get_orders_keeps_the_rest_of_the_list_around_a_notional_order():
    share_order = {**NOTIONAL_ORDER_JSON, "id": "shares-1", "qty": "3", "notional": None}
    http = _mock_http_get([share_order, NOTIONAL_ORDER_JSON])
    client = AlpacaClient(api_key="k", api_secret="s", client=http)

    orders = await client.get_orders(status="open")

    assert [(o.id, o.qty, o.notional) for o in orders] == [
        ("shares-1", Decimal("3"), None),
        ("notional-1", None, Decimal("100")),
    ]


@pytest.mark.asyncio
async def test_order_lookup_and_submit_parse_a_notional_order():
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status = MagicMock()
    response.json.return_value = NOTIONAL_ORDER_JSON
    http = AsyncMock(spec=httpx.AsyncClient)
    http.get = AsyncMock(return_value=response)
    http.post = AsyncMock(return_value=response)
    http.is_closed = False
    client = AlpacaClient(api_key="k", api_secret="s", client=http)

    found = await client.get_order_by_client_order_id("manual-notional-1")
    placed = await client.submit_order(OrderCreate(symbol="MSFT", qty=1, side="BUY"))

    assert found is not None and found.qty is None and found.notional == Decimal("100")
    assert placed.qty is None and placed.notional == Decimal("100")


@pytest.mark.asyncio
async def test_get_orders_forwards_paging_parameters():
    http = _mock_http_get([])
    client = AlpacaClient(api_key="k", api_secret="s", client=http)
    after = datetime(2026, 11, 9, 4, 59, 59, tzinfo=UTC)
    until = datetime(2026, 11, 9, 15, 0, tzinfo=UTC)

    await client.get_orders(status="all", limit=500, after=after, until=until, direction="desc")

    params = http.get.call_args.kwargs["params"]
    assert params == {
        "status": "all",
        "limit": 500,
        "after": after.isoformat(),
        "until": until.isoformat(),
        "direction": "desc",
    }


@pytest.mark.asyncio
async def test_get_orders_sends_no_paging_parameters_by_default():
    http = _mock_http_get([])
    client = AlpacaClient(api_key="k", api_secret="s", client=http)

    await client.get_orders(status="open")

    assert http.get.call_args.kwargs["params"] == {"status": "open", "limit": 50}


# --- fetch_all_orders: every matching order, past Alpaca's page limit (#177) ---


def _open_orders(count: int, *, start: datetime, prefix: str = "o") -> list[AlpacaOrderRead]:
    return [
        make_order(
            f"{prefix}{i}", symbol="MSFT", status="new", submitted_at=start - timedelta(seconds=i)
        )
        for i in range(count)
    ]


@pytest.mark.asyncio
async def test_fetch_all_orders_returns_a_short_list_in_one_request():
    book = FakeOrderBook(_open_orders(3, start=datetime.now(UTC)))

    orders = await fetch_all_orders(book, status="open")

    assert len(orders) == 3
    assert len(book.calls) == 1
    assert book.calls[0]["limit"] == ALPACA_ORDERS_PAGE_LIMIT


@pytest.mark.asyncio
async def test_fetch_all_orders_pages_past_the_page_limit():
    book = FakeOrderBook(_open_orders(1234, start=datetime.now(UTC)))

    orders = await fetch_all_orders(book, status="open")

    assert len(orders) == 1234
    assert len({order.id for order in orders}) == 1234
    assert len(book.calls) == 3


@pytest.mark.asyncio
async def test_fetch_all_orders_reads_a_tie_on_the_page_boundary_once():
    """Orders sharing the boundary timestamp are read again (until is exclusive), not lost."""
    moment = datetime.now(UTC)
    newer = _open_orders(ALPACA_ORDERS_PAGE_LIMIT - 1, start=moment)
    oldest = moment - timedelta(seconds=ALPACA_ORDERS_PAGE_LIMIT)
    tied = [
        make_order(f"tie{i}", symbol="MSFT", status="new", submitted_at=oldest) for i in range(3)
    ]
    book = FakeOrderBook(newer + tied)

    orders = await fetch_all_orders(book, status="open")

    assert len(orders) == len(newer) + 3
    assert {f"tie{i}" for i in range(3)} <= {order.id for order in orders}


@pytest.mark.asyncio
async def test_fetch_all_orders_forwards_the_after_bound():
    now = datetime.now(UTC)
    book = FakeOrderBook(_open_orders(10, start=now))
    after = now - timedelta(seconds=4.5)

    orders = await fetch_all_orders(book, status="all", after=after)

    assert len(orders) == 5
    assert book.calls[0]["after"] == after


@pytest.mark.asyncio
async def test_fetch_all_orders_fails_closed_when_paging_makes_no_progress():
    moment = datetime.now(UTC)
    same_time = [
        make_order(f"s{i}", symbol="MSFT", status="new", submitted_at=moment)
        for i in range(ALPACA_ORDERS_PAGE_LIMIT + 1)
    ]

    with pytest.raises(ValueError, match="no progress"):
        await fetch_all_orders(FakeOrderBook(same_time), status="open")


@pytest.mark.asyncio
async def test_fetch_all_orders_fails_closed_past_the_page_cap(monkeypatch):
    monkeypatch.setattr(alpaca_module, "ALPACA_ORDERS_MAX_PAGES", 2)
    book = FakeOrderBook(_open_orders(ALPACA_ORDERS_PAGE_LIMIT * 2 + 1, start=datetime.now(UTC)))

    with pytest.raises(ValueError, match="partial order list"):
        await fetch_all_orders(book, status="open")
    assert len(book.calls) == 2


@pytest.mark.asyncio
async def test_fetch_all_orders_fails_closed_on_a_full_page_without_timestamps():
    page = [
        AlpacaOrderRead(
            id=f"n{i}",
            client_order_id=f"c{i}",
            symbol="MSFT",
            qty=Decimal("1"),
            side="buy",
            type="market",
            time_in_force="day",
            status="new",
            submitted_at=None,
        )
        for i in range(ALPACA_ORDERS_PAGE_LIMIT)
    ]
    lister = SimpleNamespace(get_orders=AsyncMock(return_value=page))

    with pytest.raises(ValueError, match="submitted_at"):
        await fetch_all_orders(lister, status="open")


# --- The Orders page with a notional order (#177) ---


@pytest.mark.asyncio
async def test_orders_endpoint_lists_a_notional_order(client: AsyncClient) -> None:
    email = f"notional-{uuid.uuid4()}@example.com"
    password = "Sup3rSecret!"
    await client.post("/auth/register", json={"email": email, "password": password})
    login = await client.post("/auth/login", json={"email": email, "password": password})
    token = login.json()["access_token"]
    alpaca = AlpacaClient(
        api_key="k", api_secret="s", client=_mock_http_get([NOTIONAL_ORDER_JSON])
    )
    app.dependency_overrides[get_alpaca_client] = lambda: alpaca

    response = await client.get(
        "/api/v1/broker/orders?status=open", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    [order] = response.json()
    assert order["qty"] is None
    assert Decimal(order["notional"]) == Decimal("100")
    assert order["symbol"] == "MSFT"


# --- Router-level: protected routes require authentication ---


@pytest.mark.asyncio
async def test_get_positions_requires_auth(client: AsyncClient) -> None:
    response = await client.get("/api/v1/broker/positions")
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_get_orders_requires_auth(client: AsyncClient) -> None:
    response = await client.get("/api/v1/broker/orders")
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_place_order_is_blocked_by_global_kill_switch(monkeypatch):
    submit_order = AsyncMock()
    monkeypatch.setattr(
        "app.broker.router.is_kill_switch_enabled",
        AsyncMock(return_value=True),
    )

    with pytest.raises(HTTPException) as error:
        await place_paper_order(
            order=OrderCreate(
                symbol="AAPL",
                qty=1,
                side="BUY",
                client_order_id="blocked-order",
            ),
            current_user=object(),
            client=SimpleNamespace(submit_order=submit_order),
            db=object(),
        )

    assert error.value.status_code == 423
    submit_order.assert_not_awaited()
