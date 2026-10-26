"""Request dependencies: session principal and CSRF protection."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth.sessions import COOKIE_NAME, Principal, resolve_session
from app.db import get_db

CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "terrashield"
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    p = resolve_session(db, request.cookies.get(COOKIE_NAME))
    if p is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in required.")
    if request.method in UNSAFE and p.is_demo:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Demo sessions are read-only.")
    return p


def csrf_guard(request: Request) -> None:
    """Unsafe requests must carry a custom header that cross-site forms cannot set."""
    if request.method in UNSAFE and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing request header.")
