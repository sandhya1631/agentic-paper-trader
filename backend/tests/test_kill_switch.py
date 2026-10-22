from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.agent.router import (
    get_kill_switch,
    update_kill_switch,
)
from app.db.enums import UserRole
from app.db.models import SystemRuntimeControl
from app.risk.kill_switch import (
    EnvironmentKillSwitchOverrideError,
    is_kill_switch_enabled,
    set_kill_switch,
)
from app.schemas import KillSwitchUpdate


@pytest.mark.asyncio
async def test_database_kill_switch_state_overrides_default_off(monkeypatch):
    control = SimpleNamespace(kill_switch_enabled=True)
    result = SimpleNamespace(scalar_one_or_none=lambda: control)
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    monkeypatch.setattr(
        "app.risk.kill_switch.get_settings",
        lambda: SimpleNamespace(kill_switch_enabled=False),
    )

    assert await is_kill_switch_enabled(db) is True


@pytest.mark.asyncio
async def test_non_admin_cannot_read_or_change_global_kill_switch():
    user = SimpleNamespace(id=uuid4(), role=UserRole.OPERATOR)
    db = SimpleNamespace(execute=AsyncMock())

    with pytest.raises(HTTPException) as read_error:
        await get_kill_switch(db=db, current_user=user)
    assert read_error.value.status_code == 403

    with pytest.raises(HTTPException) as update_error:
        await update_kill_switch(
            KillSwitchUpdate(enabled=True),
            db=db,
            current_user=user,
        )
    assert update_error.value.status_code == 403
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_can_enable_persistent_global_kill_switch(monkeypatch):
    user_id = uuid4()
    monkeypatch.setattr(
        "app.agent.router.get_settings",
        lambda: SimpleNamespace(kill_switch_enabled=False),
    )
    monkeypatch.setattr(
        "app.risk.kill_switch.get_settings",
        lambda: SimpleNamespace(kill_switch_enabled=False),
    )
    db = MagicMock()
    db.execute = AsyncMock(
        return_value=SimpleNamespace(scalar_one_or_none=lambda: None)
    )
    db.add = MagicMock()
    db.commit = AsyncMock()

    async def refresh(control):
        control.updated_at = datetime.now(UTC)

    db.refresh = AsyncMock(side_effect=refresh)
    user = SimpleNamespace(id=user_id, role=UserRole.ADMIN)
    result = await update_kill_switch(
        KillSwitchUpdate(enabled=True),
        db=db,
        current_user=user,
    )

    control = db.add.call_args.args[0]
    assert isinstance(control, SystemRuntimeControl)
    assert control.key == "global"
    assert control.kill_switch_enabled is True
    assert control.updated_by == user_id
    assert result.enabled is True
    assert result.enforced_by_environment is False
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_can_read_global_kill_switch(monkeypatch):
    control = SimpleNamespace(
        kill_switch_enabled=True,
        updated_at=datetime.now(UTC),
        updated_by=uuid4(),
    )
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: control)
        )
    )
    monkeypatch.setattr(
        "app.agent.router.get_settings",
        lambda: SimpleNamespace(kill_switch_enabled=False),
    )

    result = await get_kill_switch(
        db=db,
        current_user=SimpleNamespace(id=uuid4(), role=UserRole.ADMIN),
    )

    assert result.enabled is True
    assert result.updated_by == control.updated_by


@pytest.mark.asyncio
async def test_environment_force_on_cannot_be_disabled_by_api(monkeypatch):
    user_id = uuid4()
    db = SimpleNamespace(execute=AsyncMock())
    monkeypatch.setattr(
        "app.risk.kill_switch.get_settings",
        lambda: SimpleNamespace(kill_switch_enabled=True),
    )

    with pytest.raises(EnvironmentKillSwitchOverrideError):
        await set_kill_switch(db, enabled=False, actor_id=user_id)

    db.execute.assert_not_awaited()
