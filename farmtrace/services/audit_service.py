"""Central writer for the append-only audit log."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from farmtrace.database.models import AuditLog


def record_audit(
    session: Session,
    *,
    actor_user_id: int | None,
    action: str,
    target_type: str,
    target_id: int | str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    """Add an audit entry to the current transaction (committed with the action itself)."""
    entry = AuditLog(
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=None if target_id is None else str(target_id),
        details=json.dumps(details, sort_keys=True, default=str) if details else None,
    )
    session.add(entry)
    session.flush()
    return entry
