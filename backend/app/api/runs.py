"""Runs: creation, live status, review, simulation, cancellation and reports."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.providers import readiness
from app.api.deps import principal
from app.api.scenarios import _get as get_scenario
from app.audit import audit
from app.auth.policy import require, require_run, require_workspace
from app.auth.sessions import Principal
from app.db import get_db
from app.models import (
    Approval,
    CandidatePlanRow,
    ConstraintResult,
    IdempotencyKey,
    JobLease,
    Report,
    Run,
    RunEvent,
    Scenario,
    SimulatedEvent,
    SpecialistAssessment,
)
from app.services import cancel_run, create_run, current_version, request_simulation, review_run

router = APIRouter(prefix="/api", tags=["runs"])


def run_summary(db: Session, r: Run) -> dict:
    sc = db.get(Scenario, r.scenario_id)
    stale = sc is not None and sc.current_version != r.inputs.get("scenario_version")
    return {
        "id": str(r.id),
        "workspace_id": str(r.workspace_id),
        "scenario_id": str(r.scenario_id),
        "scenario_title": r.inputs.get("title"),
        "scenario_version": r.inputs.get("scenario_version"),
        "scenario_changed_since": stale,
        "status": r.status,
        "mode": r.mode,
        "seed": r.seed,
        "model_id": r.model_id,
        "embedding_model_id": r.embedding_model_id,
        "input_hash": r.input_hash,
        "simulator_version": r.simulator_version,
        "planner_version": r.planner_version,
        "policy_version": r.policy_version,
        "workflow_version": r.workflow_version,
        "revision": r.revision,
        "blocked_reason": r.blocked_reason,
        "failure_code": r.failure_code,
        "failure_detail": r.failure_detail,
        "cancel_requested": r.cancel_requested,
        "is_demo_seed": r.is_demo_seed,
        "retry_of": str(r.retry_of) if r.retry_of else None,
        "created_at": r.created_at.isoformat(),
        "updated_at": r.updated_at.isoformat(),
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "analysis_seconds": r.analysis_seconds,
    }


class NewRun(BaseModel):
    mode: Literal["mock", "local"] = "mock"
    seed: int | None = Field(None, ge=0, le=2**31 - 1)


@router.post("/scenarios/{scenario_id}/runs", status_code=201)
def start_run(
    scenario_id: uuid.UUID,
    body: NewRun,
    p: Principal = Depends(principal),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(None, max_length=120),
) -> dict:
    require(p, "runs.create")
    sc = get_scenario(db, p, scenario_id)
    endpoint = f"POST /scenarios/{scenario_id}/runs"
    if idempotency_key and p.user_id:
        prior = db.get(IdempotencyKey, (p.user_id, idempotency_key))
        if prior is not None:
            if prior.endpoint != endpoint:
                raise HTTPException(status.HTTP_409_CONFLICT, "Idempotency key reused for a different request.")
            return prior.response
    if body.mode == "local":
        ready = readiness()
        if not ready["ready"]:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Local model is not ready. Check model readiness or start a Mock run instead."
            )
    run = create_run(db, p, sc, body.mode, body.seed)
    out = run_summary(db, run)
    if idempotency_key and p.user_id:
        db.add(IdempotencyKey(user_id=p.user_id, key=idempotency_key, endpoint=endpoint, response=out))
        try:
            db.commit()
        except IntegrityError:
            # A concurrent request with the same key won the race; return its run instead.
            db.rollback()
            prior = db.get(IdempotencyKey, (p.user_id, idempotency_key))
            if prior is None:
                raise
            return prior.response
    else:
        db.commit()
    return out


@router.get("/workspaces/{workspace_id}/runs")
def list_runs(
    workspace_id: uuid.UUID,
    p: Principal = Depends(principal),
    db: Session = Depends(get_db),
    limit: int = Query(30, ge=1, le=200),
) -> list[dict]:
    require_workspace(db, p, workspace_id)
    q = select(Run).where(Run.workspace_id == workspace_id).order_by(Run.created_at.desc()).limit(limit)
    if p.is_demo:
        q = q.where(Run.is_demo_seed.is_(True), Run.status.in_(["completed", "blocked"]))
    return [run_summary(db, r) for r in db.scalars(q)]


@router.get("/runs/{run_id}")
def get_run(run_id: uuid.UUID, p: Principal = Depends(principal), db: Session = Depends(get_db)) -> dict:
    run = require_run(db, p, run_id)
    out = run_summary(db, run)
    out["inputs"] = run.inputs
    out["evidence"] = run.evidence
    out["baseline"] = run.baseline
    out["outcome"] = run.outcome
    out["assessments"] = [
        {
            "id": str(a.id),
            "revision": a.revision,
            "round": a.round,
            "role": a.role,
            "step": a.step,
            "provider": a.provider,
            "model_id": a.model_id,
            "status": a.status,
            "output": a.output,
            "validation_errors": a.validation_errors,
            "retrieved_chunk_ids": a.retrieved_chunk_ids,
            "attempts": a.attempts,
            "latency_ms": a.latency_ms,
            "created_at": a.created_at.isoformat(),
        }
        for a in db.scalars(
            select(SpecialistAssessment)
            .where(SpecialistAssessment.run_id == run.id)
            .order_by(SpecialistAssessment.created_at)
        )
    ]
    plans = []
    for pl in db.scalars(
        select(CandidatePlanRow)
        .where(CandidatePlanRow.run_id == run.id)
        .order_by(CandidatePlanRow.revision, CandidatePlanRow.created_at)
    ):
        checks = [
            {"constraint": c.constraint, "passed": c.passed, "detail": c.detail}
            for c in db.scalars(
                select(ConstraintResult).where(ConstraintResult.plan_id == pl.id).order_by(ConstraintResult.id)
            )
        ]
        plans.append(
            {
                "id": str(pl.id),
                "revision": pl.revision,
                "label": pl.label,
                "title": pl.title,
                "method": pl.method,
                "solver_status": pl.solver_status,
                "status": pl.status,
                "rank": pl.rank,
                "actions": pl.actions,
                "objective": pl.objective,
                "plan_hash": pl.plan_hash,
                "outcome": pl.outcome,
                "sensitivity": pl.sensitivity,
                "notes": pl.notes,
                "checks": checks,
            }
        )
    out["plans"] = plans
    out["approvals"] = [
        {
            "id": str(a.id),
            "plan_id": str(a.plan_id) if a.plan_id else None,
            "decision": a.decision,
            "reason": a.reason,
            "status": a.status,
            "invalidated_reason": a.invalidated_reason,
            "decided_by": a.decider_label,
            "decided_at": a.decided_at.isoformat(),
            "scenario_version_id": str(a.scenario_version_id),
            "plan_hash": a.plan_hash,
        }
        for a in db.scalars(select(Approval).where(Approval.run_id == run.id).order_by(Approval.decided_at))
    ]
    out["has_report"] = db.scalar(select(Report.id).where(Report.run_id == run.id)) is not None
    sc = db.get(Scenario, run.scenario_id)
    out["scenario_current_version"] = sc.current_version if sc else None
    return out


@router.get("/runs/{run_id}/events")
def run_events(
    run_id: uuid.UUID, after: int = Query(0, ge=0), p: Principal = Depends(principal), db: Session = Depends(get_db)
) -> dict:
    run = require_run(db, p, run_id)
    rows = db.scalars(
        select(RunEvent).where(RunEvent.run_id == run.id, RunEvent.id > after).order_by(RunEvent.id).limit(500)
    )
    events = [
        {
            "id": e.id,
            "ts": e.ts.isoformat(),
            "actor": e.actor,
            "kind": e.kind,
            "message": e.message,
            "payload": e.payload,
        }
        for e in rows
    ]
    return {"status": run.status, "events": events}


@router.get("/runs/{run_id}/simulation")
def run_simulation(run_id: uuid.UUID, p: Principal = Depends(principal), db: Session = Depends(get_db)) -> dict:
    run = require_run(db, p, run_id)
    rows = db.scalars(select(SimulatedEvent).where(SimulatedEvent.run_id == run.id).order_by(SimulatedEvent.hour))
    return {
        "status": run.status,
        "duration_h": run.inputs["params"]["duration_h"],
        "hours": [{"hour": e.hour, **e.payload} for e in rows],
    }


class ReviewBody(BaseModel):
    decision: Literal["approved", "rejected", "revision_requested"]
    reason: str = Field(..., min_length=3, max_length=2000)
    plan_id: uuid.UUID | None = None
    excluded_actions: list[str] = Field(default_factory=list, max_length=40)
    budget_cap: float | None = Field(None, ge=0, le=500000)


@router.post("/runs/{run_id}/review")
def review(
    run_id: uuid.UUID, body: ReviewBody, p: Principal = Depends(principal), db: Session = Depends(get_db)
) -> dict:
    require(p, "plans.review")
    run = require_run(db, p, run_id)
    review_run(db, p, run, body.decision, body.reason, body.plan_id, body.excluded_actions, body.budget_cap)
    db.commit()
    db.refresh(run)
    return run_summary(db, run)


@router.post("/runs/{run_id}/simulate", status_code=202)
def simulate(run_id: uuid.UUID, p: Principal = Depends(principal), db: Session = Depends(get_db)) -> dict:
    require(p, "runs.simulate")
    run = require_run(db, p, run_id)
    request_simulation(db, p, run)
    db.commit()
    db.refresh(run)
    return run_summary(db, run)


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: uuid.UUID, p: Principal = Depends(principal), db: Session = Depends(get_db)) -> dict:
    require(p, "runs.cancel")
    run = require_run(db, p, run_id)
    result = cancel_run(db, p, run)
    db.commit()
    db.refresh(run)
    return run_summary(db, run) | {"cancel_result": result}


@router.post("/runs/{run_id}/retry", status_code=201)
def retry(run_id: uuid.UUID, body: NewRun, p: Principal = Depends(principal), db: Session = Depends(get_db)) -> dict:
    """Start a new run for the same scenario after a failure, cancellation or block."""
    require(p, "runs.create")
    run = require_run(db, p, run_id)
    if run.status not in ("failed", "cancelled", "blocked", "rejected"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Only finished runs that did not complete can be retried.")
    sc = db.get(Scenario, run.scenario_id)
    assert sc is not None
    if body.mode == "local" and not readiness()["ready"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "Local model is not ready.")
    new = create_run(
        db,
        p,
        sc,
        body.mode,
        run.seed if current_version(db, sc).id == run.scenario_version_id else None,
        retry_of=run.id,
    )
    db.commit()
    return run_summary(db, new)


@router.get("/runs/{run_id}/report")
def report(run_id: uuid.UUID, p: Principal = Depends(principal), db: Session = Depends(get_db)) -> dict:
    run = require_run(db, p, run_id)
    rep = db.scalar(select(Report).where(Report.run_id == run.id))
    if rep is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No report for this run.")
    return rep.content


@router.get("/runs/{run_id}/report.md")
def report_markdown(run_id: uuid.UUID, p: Principal = Depends(principal), db: Session = Depends(get_db)) -> Response:
    run = require_run(db, p, run_id)
    if not p.is_demo:
        require(p, "reports.export")
    rep = db.scalar(select(Report).where(Report.run_id == run.id))
    if rep is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No report for this run.")
    if not p.is_demo:
        audit(db, p, "report.exported", "run", run.id, run.workspace_id)
        db.commit()
    return Response(
        rep.markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="terrashield-report-{str(run.id)[:8]}.md"'},
    )


@router.get("/runs/{run_id}/jobs")
def run_jobs(run_id: uuid.UUID, p: Principal = Depends(principal), db: Session = Depends(get_db)) -> list[dict]:
    run = require_run(db, p, run_id)
    return [
        {
            "id": str(j.id),
            "kind": j.kind,
            "status": j.status,
            "attempts": j.attempts,
            "max_attempts": j.max_attempts,
            "last_error": j.last_error if not p.is_demo else None,
        }
        for j in db.scalars(select(JobLease).where(JobLease.run_id == run.id).order_by(JobLease.created_at))
    ]
