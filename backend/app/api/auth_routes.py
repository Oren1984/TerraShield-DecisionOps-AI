"""First-owner setup, login/logout and demo sessions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import principal
from app.audit import audit
from app.auth.passwords import DUMMY_HASH, hash_password, needs_rehash, password_problems, verify_password
from app.auth.policy import CAPABILITIES
from app.auth.sessions import COOKIE_NAME, Principal, create_session, resolve_session, revoke_session
from app.config import settings
from app.db import get_db
from app.models import User, Workspace, WorkspaceMember
from app.services import seed_workspace_scenarios

router = APIRouter(prefix="/api", tags=["auth"])


class Credentials(BaseModel):
    username: str = Field(..., min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(..., min_length=1, max_length=256)


def _set_cookie(resp: Response, token: str, hours: float) -> None:
    resp.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="strict",
        secure=settings.cookie_secure,
        max_age=int(hours * 3600),
        path="/",
    )


def _me(p: Principal) -> dict:
    return {"role": p.role, "username": p.username, "capabilities": sorted(CAPABILITIES.get(p.role, set()))}


@router.get("/setup/status")
def setup_status(db: Session = Depends(get_db)) -> dict:
    owners = db.scalar(select(func.count()).select_from(User).where(User.role == "owner")) or 0
    return {"needs_owner": owners == 0}


@router.post("/setup/owner", status_code=201)
def setup_owner(body: Credentials, resp: Response, db: Session = Depends(get_db)) -> dict:
    if db.scalar(select(func.count()).select_from(User).where(User.role == "owner")):
        raise HTTPException(status.HTTP_409_CONFLICT, "Setup is already complete.")
    problems = password_problems(body.password, body.username)
    if problems:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, " ".join(problems))
    user = User(username=body.username, password_hash=hash_password(body.password), role="owner")
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Setup is already complete.") from exc
    ws = Workspace(name="Harbor Vale Operations")
    db.add(ws)
    db.flush()
    db.add(WorkspaceMember(user_id=user.id, workspace_id=ws.id))
    seed_workspace_scenarios(db, ws.id, user.id)
    token, row = create_session(db, "owner", user)
    p = Principal(row.id, "owner", user.id, user.username)
    audit(db, p, "setup.owner_created", "user", user.id)
    db.commit()
    _set_cookie(resp, token, settings.session_ttl_hours)
    return _me(p)


@router.post("/auth/login")
def login(body: Credentials, resp: Response, db: Session = Depends(get_db)) -> dict:
    user = db.scalar(select(User).where(User.username == body.username))
    now = datetime.now(UTC)
    generic = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password.")
    if user is None:
        verify_password(DUMMY_HASH, body.password)  # equalise timing
        raise generic
    if user.locked_until and user.locked_until > now:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed attempts. Try again later.")
    if not user.is_active or not verify_password(user.password_hash, body.password):
        user.failed_logins += 1
        if user.failed_logins >= settings.login_max_failures:
            user.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
            user.failed_logins = 0
        audit(db, None, "auth.login_failed", "user", user.id)
        db.commit()
        raise generic
    user.failed_logins = 0
    user.locked_until = None
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    token, row = create_session(db, user.role, user)
    p = Principal(row.id, user.role, user.id, user.username)
    audit(db, p, "auth.login", "user", user.id)
    db.commit()
    _set_cookie(resp, token, settings.session_ttl_hours)
    return _me(p)


@router.post("/auth/demo")
def demo(resp: Response, db: Session = Depends(get_db)) -> dict:
    token, row = create_session(db, "demo", None)
    p = Principal(row.id, "demo", None, None)
    audit(db, p, "auth.demo_session", "session", row.id)
    db.commit()
    _set_cookie(resp, token, settings.demo_session_ttl_hours)
    return _me(p)


@router.post("/auth/logout")
def logout(request: Request, resp: Response, db: Session = Depends(get_db)) -> dict:
    token = request.cookies.get(COOKIE_NAME)
    p = resolve_session(db, token)
    revoke_session(db, token)
    if p:
        audit(db, p, "auth.logout", "session", p.session_id)
    db.commit()
    resp.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(p: Principal = Depends(principal)) -> dict:
    return _me(p)
