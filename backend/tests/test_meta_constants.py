"""Tests for GET /meta/constants - backend enums/constants the frontend
should read instead of hardcoding (#139)."""

import pytest
from httpx import AsyncClient

from app.db.enums import (
    ApprovalMode,
    ApprovalStatus,
    CycleStatus,
    CycleTrigger,
    DecisionAction,
    PolicyDecisionType,
)


@pytest.mark.asyncio
async def test_get_constants_is_public(client: AsyncClient):
    """No auth required - this is non-sensitive, static metadata."""
    response = await client.get("/meta/constants")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_constants_matches_backend_enums(client: AsyncClient):
    response = await client.get("/meta/constants")
    body = response.json()

    assert body["currency"] == "USD"
    assert set(body["cycle_trigger"]) == {v.value for v in CycleTrigger}
    assert set(body["cycle_status"]) == {v.value for v in CycleStatus}
    assert set(body["decision_action"]) == {v.value for v in DecisionAction}
    assert set(body["policy_decision_type"]) == {v.value for v in PolicyDecisionType}
    assert set(body["approval_mode"]) == {v.value for v in ApprovalMode}
    assert set(body["approval_status"]) == {v.value for v in ApprovalStatus}
