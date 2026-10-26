"""Server-side sessions. The cookie carries an opaque token; only its SHA-256 is stored."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AuthSession, User

COOKIE_NAME = "ts_session"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class Principal:
    session_id: uuid.UUID
    role: str  # owner | operator | demo
    user_id: uuid.UUID | None
    username: str | None

    @property
    def is_demo(self) -> bool:
        return self.role == "demo"


def create_session(db: Session, role: str, user: User | None) -> tuple[str, AuthSession]:
    token = secrets.token_urlsafe(32)
    ttl = settings.demo_session_ttl_hours if role == "demo" else settings.session_ttl_hours
    now = datetime.now(UTC)
    row = AuthSession(
        token_hash=_hash(token),
        user_id=user.id if user else None,
        role=role,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(hours=ttl),
    )
    db.add(row)
    db.flush()
    return token, row


def resolve_session(db: Session, token: str | None) -> Principal | None:
    if not token:
        return None
    row = db.scalar(select(AuthSession).where(AuthSession.token_hash == _hash(token)))
    if row is None or row.revoked_at is not None:
        return None
    now = datetime.now(UTC)
    idle_limit = row.last_seen_at + timedelta(minutes=settings.session_idle_minutes)
    if now >= row.expires_at or now >= idle_limit:
        row.revoked_at = now
        db.commit()
        return None
    username = None
    if row.user_id is not None:
        user = db.get(User, row.user_id)
        if user is None or not user.is_active or user.role != row.role:
            row.revoked_at = now
            db.commit()
            return None
        username = user.username
    if (now - row.last_seen_at).total_seconds() > 60:
        row.last_seen_at = now
        db.commit()
    return Principal(session_id=row.id, role=row.role, user_id=row.user_id, username=username)


def revoke_session(db: Session, token: str | None) -> None:
    if not token:
        return
    row = db.scalar(select(AuthSession).where(AuthSession.token_hash == _hash(token)))
    if row and row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)


def revoke_user_sessions(db: Session, user_id: uuid.UUID) -> None:
    now = datetime.now(UTC)
    for row in db.scalars(select(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))):
        row.revoked_at = now
