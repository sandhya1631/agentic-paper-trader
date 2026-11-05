from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from app.agent.router import (
    get_kill_switch,
    list_kill_switch_audit,
    update_kill_switch,
)
from app.db.enums import KillSwitchScope, UserRole
from app.db.models import KillSwitchEvent, SystemRuntimeControl, User
from app.risk.kill_switch import (
    OUTCOME_APPLIED,
    OUTCOME_REJECTED_ENV_OVERRIDE,
    EnvironmentKillSwitchOverrideError,
    is_kill_switch_enabled,
    set_kill_switch,
    set_user_scheduler_switch,
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
            KillSwitchUpdate(enabled=True, reason="drill"),
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
        KillSwitchUpdate(enabled=True, reason="Unexpected trades"),
        db=db,
        current_user=user,
    )

    control = db.add.call_args_list[0].args[0]
    assert isinstance(control, SystemRuntimeControl)
    assert control.key == "global"
    assert control.kill_switch_enabled is True
    assert control.updated_by == user_id
    assert result.enabled is True
    assert result.enforced_by_environment is False
    assert result.reason == "Unexpected trades"
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
            side_effect=[
                SimpleNamespace(scalar_one_or_none=lambda: control),
                SimpleNamespace(scalar_one_or_none=lambda: "Unexpected trades"),
            ]
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
    assert result.reason == "Unexpected trades"


@pytest.mark.asyncio
async def test_environment_force_on_cannot_be_disabled_by_api(monkeypatch):
    user_id = uuid4()
    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    monkeypatch.setattr(
        "app.risk.kill_switch.get_settings",
        lambda: SimpleNamespace(kill_switch_enabled=True),
    )

    with pytest.raises(EnvironmentKillSwitchOverrideError):
        await set_kill_switch(db, enabled=False, actor_id=user_id)

    db.execute.assert_not_awaited()




def test_activation_requires_a_reason():
    with pytest.raises(ValidationError):
        KillSwitchUpdate(enabled=True)
    with pytest.raises(ValidationError):
        KillSwitchUpdate(enabled=True, reason="")
    with pytest.raises(ValidationError):
        KillSwitchUpdate(enabled=True, reason="   ")


def test_reason_is_trimmed_and_limited_to_500_characters():
    assert KillSwitchUpdate(enabled=True, reason="  halt trading  ").reason == "halt trading"
    with pytest.raises(ValidationError):
        KillSwitchUpdate(enabled=True, reason="x" * 501)


def test_deactivation_does_not_need_a_reason():
    update = KillSwitchUpdate(enabled=False)
    assert update.reason is None


def _db_for_toggle(existing):
    db = MagicMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: existing))
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


def _events(db):
    return [c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], KillSwitchEvent)]


@pytest.mark.asyncio
async def test_activation_is_audited_with_actor_reason_and_transition(monkeypatch):
    monkeypatch.setattr(
        "app.risk.kill_switch.get_settings", lambda: SimpleNamespace(kill_switch_enabled=False)
    )
    actor = uuid4()
    db = _db_for_toggle(None)

    await set_kill_switch(db, enabled=True, actor_id=actor, reason="Unexpected trades")

    (event,) = _events(db)
    assert event.actor_id == actor
    assert event.reason == "Unexpected trades"
    assert event.previous_enabled is False
    assert event.new_enabled is True
    assert event.outcome == OUTCOME_APPLIED
    db.commit.assert_awaited_once()  # state change and audit row commit together


@pytest.mark.asyncio
async def test_deactivation_is_audited_too(monkeypatch):
    monkeypatch.setattr(
        "app.risk.kill_switch.get_settings", lambda: SimpleNamespace(kill_switch_enabled=False)
    )
    actor = uuid4()
    control = SimpleNamespace(kill_switch_enabled=True, updated_by=None)
    db = _db_for_toggle(control)

    await set_kill_switch(db, enabled=False, actor_id=actor, reason="All clear")

    (event,) = _events(db)
    assert event.previous_enabled is True
    assert event.new_enabled is False
    assert event.reason == "All clear"
    assert control.kill_switch_enabled is False


@pytest.mark.asyncio
async def test_refused_environment_override_attempt_is_audited(monkeypatch):
    monkeypatch.setattr(
        "app.risk.kill_switch.get_settings", lambda: SimpleNamespace(kill_switch_enabled=True)
    )
    actor = uuid4()
    db = _db_for_toggle(None)

    with pytest.raises(EnvironmentKillSwitchOverrideError):
        await set_kill_switch(db, enabled=False, actor_id=actor, reason="try to resume")

    (event,) = _events(db)
    assert event.actor_id == actor
    assert event.outcome == OUTCOME_REJECTED_ENV_OVERRIDE
    assert event.new_enabled is False
    db.commit.assert_awaited_once()  # the attempt is saved even though the request fails


@pytest.mark.asyncio
async def test_two_toggles_are_both_audited(monkeypatch):
    monkeypatch.setattr(
        "app.risk.kill_switch.get_settings", lambda: SimpleNamespace(kill_switch_enabled=False)
    )
    db = _db_for_toggle(SimpleNamespace(kill_switch_enabled=False, updated_by=None))

    await set_kill_switch(db, enabled=True, actor_id=uuid4(), reason="first admin")
    await set_kill_switch(db, enabled=True, actor_id=uuid4(), reason="second admin")

    assert [e.reason for e in _events(db)] == ["first admin", "second admin"]


@pytest.mark.asyncio
async def test_audit_trail_endpoint_is_admin_only_and_lists_events():
    actor_id = uuid4()
    event = SimpleNamespace(
        id=uuid4(),
        occurred_at=datetime.now(UTC),
        actor_id=actor_id,
        previous_enabled=False,
        new_enabled=True,
        outcome=OUTCOME_APPLIED,
        reason="Unexpected trades",
        scope="GLOBAL",
        target_user_id=None,
    )
    events_result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [event]))
    email_row = SimpleNamespace(id=actor_id, email="admin@example.com")
    emails_result = [email_row]
    db = SimpleNamespace(execute=AsyncMock(side_effect=[events_result, emails_result]))

    with pytest.raises(HTTPException) as denied:
        await list_kill_switch_audit(
            limit=50, db=db, current_user=SimpleNamespace(id=uuid4(), role=UserRole.OPERATOR)
        )
    assert denied.value.status_code == 403

    rows = await list_kill_switch_audit(
        limit=50, db=db, current_user=SimpleNamespace(id=uuid4(), role=UserRole.ADMIN)
    )
    assert len(rows) == 1
    assert rows[0].reason == "Unexpected trades"
    assert rows[0].new_enabled is True
    assert rows[0].actor_email == "admin@example.com"


def _make_user(*, scheduler_enabled: bool) -> User:
    return User(
        email=f"{uuid4()}@example.com",
        password_hash="not-a-real-hash",
        role=UserRole.OPERATOR,
        scheduler_enabled=scheduler_enabled,
    )


@pytest.mark.asyncio
async def test_user_scheduler_switch_pauses_and_audits_as_user_scope(db_session):
    """Stopping a user's scheduler flips the flag and writes a USER-scope event whose
    polarity means "trading halted", same as the global switch."""
    user = _make_user(scheduler_enabled=True)
    db_session.add(user)
    await db_session.flush()

    await set_user_scheduler_switch(db_session, user=user, scheduler_enabled=False)
    await db_session.commit()

    assert user.scheduler_enabled is False
    events = (await db_session.execute(select(KillSwitchEvent))).scalars().all()
    (event,) = events
    assert event.scope == KillSwitchScope.USER.value
    assert event.target_user_id == user.id
    assert event.actor_id == user.id
    assert event.previous_enabled is False  # was running -> not halted
    assert event.new_enabled is True  # now paused -> halted


@pytest.mark.asyncio
async def test_user_scheduler_switch_resume_is_also_audited(db_session):
    user = _make_user(scheduler_enabled=False)
    db_session.add(user)
    await db_session.flush()

    await set_user_scheduler_switch(db_session, user=user, scheduler_enabled=True)
    await db_session.commit()

    assert user.scheduler_enabled is True
    events = (await db_session.execute(select(KillSwitchEvent))).scalars().all()
    (event,) = events
    assert event.previous_enabled is True  # was paused -> halted
    assert event.new_enabled is False  # now running -> not halted


@pytest.mark.asyncio
async def test_user_scheduler_switch_is_a_no_op_when_unchanged(db_session):
    """Starting a schedule for a user whose scheduler is already enabled writes no
    audit row, matching the admin switch's no-op behavior elsewhere in this codebase."""
    user = _make_user(scheduler_enabled=True)
    db_session.add(user)
    await db_session.flush()

    await set_user_scheduler_switch(db_session, user=user, scheduler_enabled=True)
    await db_session.commit()

    assert user.scheduler_enabled is True
    events = (await db_session.execute(select(KillSwitchEvent))).scalars().all()
    assert events == []


@pytest.mark.asyncio
async def test_user_and_global_kill_switch_events_are_independently_scoped(db_session):
    """A user pausing their own scheduler must never read as, or be confused with, the
    admin global halt - the two live in the same audit table but stay distinguishable."""
    user = _make_user(scheduler_enabled=True)
    db_session.add(user)
    await db_session.flush()

    await set_user_scheduler_switch(db_session, user=user, scheduler_enabled=False)
    await set_kill_switch(db_session, enabled=True, actor_id=user.id, reason="global halt drill")

    events = (
        (await db_session.execute(select(KillSwitchEvent).order_by(KillSwitchEvent.occurred_at)))
        .scalars()
        .all()
    )
    assert [e.scope for e in events] == [KillSwitchScope.USER.value, KillSwitchScope.GLOBAL.value]
    assert events[0].target_user_id == user.id
    assert events[1].target_user_id is None


@pytest.mark.asyncio
async def test_audit_endpoint_denormalizes_emails_over_http(client, db_session) -> None:
    """End-to-end (#169): the admin audit endpoint resolves actor_id/target_user_id
    to real emails in one extra query, so the UI never has to do it itself."""
    operator_email = f"operator-{uuid4()}@example.com"
    admin_email = f"admin-{uuid4()}@example.com"
    password = "S3cure-Password!1"

    await client.post("/auth/register", json={"email": operator_email, "password": password})
    await client.post("/auth/register", json={"email": admin_email, "password": password})

    operator = (
        await db_session.execute(select(User).where(User.email == operator_email))
    ).scalar_one()
    admin = (await db_session.execute(select(User).where(User.email == admin_email))).scalar_one()
    admin.role = UserRole.ADMIN
    await db_session.commit()

    # One USER-scope event (the operator pausing their own scheduler) ...
    await set_user_scheduler_switch(db_session, user=operator, scheduler_enabled=False)
    await db_session.commit()
    # ... and one GLOBAL event by the admin.
    await set_kill_switch(db_session, enabled=True, actor_id=admin.id, reason="drill")

    login = await client.post("/auth/login", json={"email": admin_email, "password": password})
    admin_token = login.json()["access_token"]

    response = await client.get(
        "/agent/kill-switch/events", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    rows = {row["scope"]: row for row in response.json()}

    assert rows["USER"]["actor_email"] == operator_email
    assert rows["USER"]["target_user_email"] == operator_email
    assert rows["GLOBAL"]["actor_email"] == admin_email
    assert rows["GLOBAL"]["target_user_email"] is None