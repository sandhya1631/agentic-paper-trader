"""Generic audit-event writer (#141).

Audit rows are only ever added. `record_audit_event` stages the row with `db.add`
and never commits: the caller commits once, so the audit record lands in the SAME
transaction as the change it describes (the kill-switch audit principle, see
app/risk/kill_switch.py). A change that rolls back therefore leaves no audit row, and
a committed change always has one.
"""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import AuditActorType
from app.db.models import AuditEvent


def record_audit_event(
    db: AsyncSession,
    *,
    event_type: str,
    entity_type: str,
    entity_id: uuid.UUID | None,
    actor_id: uuid.UUID | None,
    actor_type: AuditActorType = AuditActorType.USER,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    """Stage one AuditEvent on the session; the caller commits it with the change."""
    event = AuditEvent(
        actor_type=actor_type.value,
        actor_id=actor_id,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        details=dict(details or {}),
    )
    db.add(event)
    return event
