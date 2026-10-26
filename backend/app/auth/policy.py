"""Server-side authorization policy.

All permission decisions are made here; the UI only mirrors them.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.sessions import Principal
from app.models import Run, Workspace, WorkspaceMember

# Capability matrix (policy-1.0.0)
CAPABILITIES: dict[str, set[str]] = {
    "owner": {
        "users.manage",
        "workspaces.manage",
        "scenarios.write",
        "runs.create",
        "runs.cancel",
        "plans.review",
        "runs.simulate",
        "reports.export",
        "evidence.manage",
        "ops.view",
    },
    "operator": {"scenarios.write", "runs.create", "runs.cancel", "reports.export"},
    "demo": set(),
}


def require(p: Principal, capability: str) -> None:
    if capability not in CAPABILITIES.get(p.role, set()):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Role '{p.role}' is not permitted to {capability}.")


def require_mutation(p: Principal) -> None:
    if p.is_demo:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Demo sessions are read-only.")


def visible_workspace_ids(db: Session, p: Principal) -> list[uuid.UUID]:
    if p.role == "owner":
        return list(db.scalars(select(Workspace.id).where(Workspace.is_demo.is_(False))))
    if p.role == "demo":
        return list(db.scalars(select(Workspace.id).where(Workspace.is_demo.is_(True))))
    return list(db.scalars(select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == p.user_id)))


def require_workspace(db: Session, p: Principal, workspace_id: uuid.UUID) -> Workspace:
    ws = db.get(Workspace, workspace_id)
    # Same response for "missing" and "not permitted" to avoid leaking existence.
    if ws is None or workspace_id not in visible_workspace_ids(db, p):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workspace not found.")
    return ws


def require_run(db: Session, p: Principal, run_id: uuid.UUID) -> Run:
    run = db.get(Run, run_id)
    if run is None or run.workspace_id not in visible_workspace_ids(db, p):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found.")
    if p.is_demo and not (run.is_demo_seed and run.status in ("completed", "blocked")):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found.")
    return run
