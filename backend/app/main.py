"""FastAPI application."""

from __future__ import annotations

import logging
import time
import uuid

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import auth_routes, meta, runs, scenarios, users
from app.api.deps import csrf_guard
from app.config import settings
from app.db import SessionLocal
from app.logging_setup import configure_logging, log, request_id_var
from app.simulation.district import load_district

configure_logging(settings.log_level)
logger = logging.getLogger("terrashield.api")

app = FastAPI(
    title="TerraShield DecisionOps AI",
    description="Local-first simulation POC. Not an operational forecasting or emergency system.",
    version="1.0.0",
    dependencies=[Depends(csrf_guard)],
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url=None,
)
for r in (auth_routes.router, users.router, scenarios.router, runs.router, meta.router):
    app.include_router(r)


@app.middleware("http")
async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    token = request_id_var.set(rid)
    start = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers["X-Request-ID"] = rid
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    if not request.url.path.startswith("/health"):
        token = request_id_var.set(rid)
        log(
            logger,
            logging.INFO,
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            ms=round((time.perf_counter() - start) * 1000, 1),
        )
        request_id_var.reset(token)
    return response


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error")
    return JSONResponse({"detail": "Internal error. See server logs with the request id."}, status_code=500)


def _head_revision() -> str:
    cfg = Config()
    cfg.set_main_option("script_location", "alembic")
    return ScriptDirectory.from_config(cfg).get_current_head() or ""


@app.get("/health/live")
def live() -> dict:
    return {"status": "ok"}


@app.get("/health/ready")
def ready() -> JSONResponse:
    checks: dict[str, object] = {}
    ok = True
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
            current = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
            checks["database"] = "ok"
            head = _head_revision()
            checks["migrations"] = "ok" if current == head else f"at {current}, expected {head}"
            ok &= current == head
            ext = db.execute(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")).scalar()
            checks["pgvector"] = ext or "missing"
            ok &= ext is not None
    except Exception as exc:  # noqa: BLE001 - readiness reports any failure
        checks["database"] = f"error: {type(exc).__name__}"
        ok = False
    try:
        load_district()
        checks["district"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["district"] = f"error: {type(exc).__name__}"
        ok = False
    return JSONResponse({"status": "ready" if ok else "not_ready", "checks": checks}, status_code=200 if ok else 503)
