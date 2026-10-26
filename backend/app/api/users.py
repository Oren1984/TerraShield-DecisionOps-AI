"""Local user and workspace management (owner only)."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import principal
from app.audit import audit
from app.auth.passwords import hash_password, password_problems
from app.auth.policy import require, require_workspace, visible_workspace_ids
from app.auth.sessions import Principal, revoke_user_sessions
from app.db import get_db
from app.models import Run, Scenario, User, Workspace, WorkspaceMember
from app.services import seed_workspace_scenarios

router = APIRouter(prefix="/api", tags=["users"])


class NewUser(BaseModel):
    username: str = Field(..., min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(..., min_length=1, max_length=256)
    role: Literal["owner", "operator"] = "operator"
    workspace_ids: list[uuid.UUID] = Field(default_factory=list)


class UserPatch(BaseModel):
    is_active: bool | None = None
    password: str | None = Field(None, max_length=256)
    workspace_ids: list[uuid.UUID] | None = None


def _user_out(db: Session, u: User) -> dict:
    ws = list(db.scalars(select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == u.id)))
    return {
        "id": str(u.id),
        "username": u.username,
        "role": u.role,
        "is_active": u.is_active,
        "workspace_ids": [str(w) for w in ws],
        "created_at": u.created_at.isoformat(),
    }


@router.get("/users")
def list_users(p: Principal = Depends(principal), db: Session = Depends(get_db)) -> list[dict]:
    require(p, "users.manage")
    return [_user_out(db, u) for u in db.scalars(select(User).order_by(User.created_at))]


@router.post("/users", status_code=201)
def create_user(body: NewUser, p: Principal = Depends(principal), db: Session = Depends(get_db)) -> dict:
    require(p, "users.manage")
    problems = password_problems(body.password, body.username)
    if problems:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, " ".join(problems))
    u = User(username=body.username, password_hash=hash_password(body.password), role=body.role)
    db.add(u)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already exists.") from exc
    for wid in body.workspace_ids:
        require_workspace(db, p, wid)
        db.add(WorkspaceMember(user_id=u.id, workspace_id=wid))
    audit(db, p, "user.created", "user", u.id, role=body.role)
    db.commit()
    return _user_out(db, u)


@router.patch("/users/{user_id}")
def patch_user(
    user_id: uuid.UUID, body: UserPatch, p: Principal = Depends(principal), db: Session = Depends(get_db)
) -> dict:
    require(p, "users.manage")
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    if body.is_active is not None:
        if u.id == p.user_id and not body.is_active:
            raise HTTPException(status.HTTP_409_CONFLICT, "You cannot deactivate your own account.")
        u.is_active = body.is_active
        if not body.is_active:
            revoke_user_sessions(db, u.id)
    if body.password is not None:
        problems = password_problems(body.password, u.username)
        if problems:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, " ".join(problems))
        u.password_hash = hash_password(body.password)
        revoke_user_sessions(db, u.id)
    if body.workspace_ids is not None:
        for m in db.scalars(select(WorkspaceMember).where(WorkspaceMember.user_id == u.id)):
            db.delete(m)
        db.flush()
        for wid in body.workspace_ids:
            require_workspace(db, p, wid)
            db.add(WorkspaceMember(user_id=u.id, workspace_id=wid))
    audit(
        db,
        p,
        "user.updated",
        "user",
        u.id,
        fields=sorted(body.model_dump(exclude_none=True, exclude={"password"})),
        password_changed=body.password is not None,
    )
    db.commit()
    return _user_out(db, u)


class NewWorkspace(BaseModel):
    name: str = Field(..., min_length=3, max_length=120)
    seed_scenarios: bool = True


@router.get("/workspaces")
def list_workspaces(p: Principal = Depends(principal), db: Session = Depends(get_db)) -> list[dict]:
    ids = visible_workspace_ids(db, p)
    out = []
    for ws in db.scalars(select(Workspace).where(Workspace.id.in_(ids)).order_by(Workspace.created_at)):
        n_sc = len(list(db.scalars(select(Scenario.id).where(Scenario.workspace_id == ws.id))))
        n_runs = len(list(db.scalars(select(Run.id).where(Run.workspace_id == ws.id))))
        out.append({"id": str(ws.id), "name": ws.name, "is_demo": ws.is_demo, "scenarios": n_sc, "runs": n_runs})
    return out


@router.post("/workspaces", status_code=201)
def create_workspace(body: NewWorkspace, p: Principal = Depends(principal), db: Session = Depends(get_db)) -> dict:
    require(p, "workspaces.manage")
    ws = Workspace(name=body.name)
    db.add(ws)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Workspace name already exists.") from exc
    if body.seed_scenarios:
        seed_workspace_scenarios(db, ws.id, p.user_id)
    audit(db, p, "workspace.created", "workspace", ws.id, ws.id)
    db.commit()
    return {"id": str(ws.id), "name": ws.name, "is_demo": False}
