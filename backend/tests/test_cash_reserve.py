"""Tests for cash/reserve validation (Story 3.1.3 / Issue #30)."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.risk.cash_reserve import (
    check_sufficient_cash,
    execute_with_cash_check,
    fetch_cash_and_equity_for,
    validate_cash_reserve,
)


def _account_fetcher(cash: Decimal, equity: Decimal):
    async def fetch() -> tuple[Decimal, Decimal]:
        return cash, equity

    return fetch


# --- pure check ---


def test_check_sufficient_cash_allows_within_limit():
    result = check_sufficient_cash(
        cash=Decimal("10000"),
        equity=Decimal("10000"),
        cash_reserve_pct=Decimal("0.05"),
        order_cost=Decimal("9000"),
    )
    assert result.allowed is True
    assert result.available_after_reserve == Decimal("9500.00")


def test_check_sufficient_cash_allows_at_exact_boundary():
    # cash=10000, equity=10000, reserve=5% -> available exactly 9500
    result = check_sufficient_cash(
        cash=Decimal("10000"),
        equity=Decimal("10000"),
        cash_reserve_pct=Decimal("0.05"),
        order_cost=Decimal("9500.00"),
    )
    assert result.allowed is True


def test_check_sufficient_cash_rejects_over_limit():
    result = check_sufficient_cash(
        cash=Decimal("10000"),
        equity=Decimal("10000"),
        cash_reserve_pct=Decimal("0.05"),
        order_cost=Decimal("9501"),
    )
    assert result.allowed is False
    assert "exceeds available cash" in result.reason


# --- fetching real account data ---


@pytest.mark.asyncio
async def test_fetch_cash_and_equity_for_calls_get_account():
    fake_account = MagicMock(cash=Decimal("5000"), equity=Decimal("6000"))
    alpaca = MagicMock()
    alpaca.get_account = AsyncMock(return_value=fake_account)

    cash, equity = await fetch_cash_and_equity_for(alpaca)

    assert cash == Decimal("5000")
    assert equity == Decimal("6000")
    alpaca.get_account.assert_awaited_once()


# --- validate (fetch + check) ---


@pytest.mark.asyncio
async def test_validate_cash_reserve_sufficient():
    result = await validate_cash_reserve(
        _account_fetcher(Decimal("10000"), Decimal("10000")),
        cash_reserve_pct=Decimal("0.05"),
        order_cost=Decimal("100"),
    )
    assert result.allowed is True


@pytest.mark.asyncio
async def test_validate_cash_reserve_insufficient():
    result = await validate_cash_reserve(
        _account_fetcher(Decimal("100"), Decimal("100")),
        cash_reserve_pct=Decimal("0.05"),
        order_cost=Decimal("9999"),
    )
    assert result.allowed is False


@pytest.mark.asyncio
async def test_account_fetch_failure_fails_closed():
    async def broken_fetch() -> tuple[Decimal, Decimal]:
        raise ConnectionError("alpaca down")

    result = await validate_cash_reserve(
        broken_fetch, cash_reserve_pct=Decimal("0.05"), order_cost=Decimal("100")
    )

    assert result.allowed is False
    assert "could not verify" in result.reason.lower()
    assert result.available_after_reserve is None


# --- reject BEFORE broker submission ---


@pytest.mark.asyncio
async def test_insufficient_cash_never_calls_broker():
    submit = AsyncMock(return_value="order")

    result, order = await execute_with_cash_check(
        _account_fetcher(Decimal("100"), Decimal("100")),
        cash_reserve_pct=Decimal("0.05"),
        order_cost=Decimal("9999"),
        submit_order=submit,
    )

    assert result.allowed is False
    assert order is None
    submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_sufficient_cash_calls_broker():
    submit = AsyncMock(return_value="order")

    result, order = await execute_with_cash_check(
        _account_fetcher(Decimal("10000"), Decimal("10000")),
        cash_reserve_pct=Decimal("0.05"),
        order_cost=Decimal("100"),
        submit_order=submit,
    )

    assert result.allowed is True
    assert order == "order"
    submit.assert_awaited_once()
