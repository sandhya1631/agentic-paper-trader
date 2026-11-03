"""Kill-switch audit trail against a real database (Story #72)."""

import pytest
from sqlalchemy import select

from app.db.enums import UserRole
from app.db.models import KillSwitchEvent, SystemRuntimeControl, User
from app.risk import kill_switch
from app.risk.kill_switch import (
    OUTCOME_APPLIED,
    OUTCOME_REJECTED_ENV_OVERRIDE,
    EnvironmentKillSwitchOverrideError,
    get_latest_activation_reason,
    list_kill_switch_events,
    set_kill_switch,
)


async def _admin(db_session) -> User:
    user = User(email="admin@example.com", password_hash="hash", role=UserRole.ADMIN)
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.mark.asyncio
async def test_activation_and_deactivation_are_stored_newest_first(db_session):
    admin = await _admin(db_session)

    await set_kill_switch(db_session, enabled=True, actor_id=admin.id, reason="Unexpected trades")
    await set_kill_switch(db_session, enabled=False, actor_id=admin.id, reason="All clear")

    events = await list_kill_switch_events(db_session)
    assert [(e.new_enabled, e.reason) for e in events] == [
        (False, "All clear"),
        (True, "Unexpected trades"),
    ]
    assert events[0].previous_enabled is True
    assert events[1].previous_enabled is False
    assert all(e.actor_id == admin.id and e.outcome == OUTCOME_APPLIED for e in events)
    assert all(e.occurred_at is not None for e in events)


@pytest.mark.asyncio
async def test_latest_activation_reason_is_returned(db_session):
    admin = await _admin(db_session)
    assert await get_latest_activation_reason(db_session) is None

    await set_kill_switch(db_session, enabled=True, actor_id=admin.id, reason="first")
    await set_kill_switch(db_session, enabled=False, actor_id=admin.id)
    await set_kill_switch(db_session, enabled=True, actor_id=admin.id, reason="second")

    assert await get_latest_activation_reason(db_session) == "second"


@pytest.mark.asyncio
async def test_refused_override_attempt_is_saved_and_state_is_unchanged(db_session, monkeypatch):
    admin = await _admin(db_session)
    monkeypatch.setattr(
        kill_switch, "get_settings", lambda: type("S", (), {"kill_switch_enabled": True})()
    )

    with pytest.raises(EnvironmentKillSwitchOverrideError):
        await set_kill_switch(db_session, enabled=False, actor_id=admin.id, reason="resume")

    rows = (await db_session.execute(select(KillSwitchEvent))).scalars().all()
    assert len(rows) == 1
    assert rows[0].outcome == OUTCOME_REJECTED_ENV_OVERRIDE
    assert rows[0].reason == "resume"
    assert (await db_session.execute(select(SystemRuntimeControl))).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_audit_row_survives_when_the_actor_is_deleted(db_session):
    admin = await _admin(db_session)
    await set_kill_switch(db_session, enabled=True, actor_id=admin.id, reason="drill")

    await db_session.delete(admin)
    await db_session.commit()

    (event,) = await list_kill_switch_events(db_session)
    assert event.actor_id is None
    assert event.reason == "drill"