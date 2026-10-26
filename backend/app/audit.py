"""Audit and run-trace helpers."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.auth.sessions import Principal
from app.models import AuditEvent, RunEvent


def audit(
    db: Session,
    p: Principal | None,
    action: str,
    target_type: str,
    target_id: str | uuid.UUID | None = None,
    workspace_id: uuid.UUID | None = None,
    **detail: Any,
) -> None:
    db.add(
        AuditEvent(
            actor_user_id=p.user_id if p else None,
            actor_role=p.role if p else "system",
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            workspace_id=workspace_id,
            detail=detail,
        )
    )


def trace(db: Session, run_id: uuid.UUID, actor: str, kind: str, message: str = "", **payload: Any) -> None:
    db.add(RunEvent(run_id=run_id, actor=actor, kind=kind, message=message, payload=payload))
