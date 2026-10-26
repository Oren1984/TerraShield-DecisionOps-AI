"""Reference data, model readiness, operations status and evidence library."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.providers import readiness
from app.agents.retrieval import (
    FIXTURE_MODEL_ID,
    embed_chunks,
    embedding_status,
    ensure_fixture_model,
    ingest_document,
    ollama_model_id,
    retrieve,
)
from app.api.deps import principal
from app.audit import audit
from app.auth.policy import require, require_run, require_workspace
from app.auth.sessions import Principal
from app.config import settings
from app.db import get_db
from app.jobs import queue
from app.models import JobLease, Report, Run, RunEvent, SpecialistAssessment
from app.planning.planner import RANKING
from app.simulation.actions import catalog_payload
from app.simulation.district import load_district
from app.simulation.engine import (
    ACCESS_PENALTY,
    GROUND_CLOSURE_SI,
    GROUND_WARNING_SI,
    OVERTOP_COEFF,
    ROAD_FLOOD_CLOSURE_M,
    SEVERITY,
)
from app.simulation.scenario import ScenarioParams
from app.versions import PLANNER_VERSION, POLICY_VERSION, SIMULATOR_VERSION, WORKFLOW_VERSION

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/district")
def district(_: Principal = Depends(principal)) -> dict:
    return load_district().raw


@router.get("/catalog")
def catalog(_: Principal = Depends(principal)) -> dict:
    return {
        "actions": catalog_payload(load_district()),
        "scenario_schema": ScenarioParams.model_json_schema(),
        "ranking": RANKING,
        "model_constants": {
            "overtopping_coefficient": OVERTOP_COEFF,
            "road_flood_closure_m": ROAD_FLOOD_CLOSURE_M,
            "ground_closure_si": GROUND_CLOSURE_SI,
            "ground_warning_si": GROUND_WARNING_SI,
            "severity_weights": SEVERITY,
            "access_penalty": ACCESS_PENALTY,
        },
        "versions": {
            "simulator": SIMULATOR_VERSION,
            "planner": PLANNER_VERSION,
            "policy": POLICY_VERSION,
            "workflow": WORKFLOW_VERSION,
        },
    }


@router.get("/models/readiness")
def model_readiness(p: Principal = Depends(principal), db: Session = Depends(get_db)) -> dict:
    out: dict = {
        "default_mode": settings.default_mode,
        "mock": {"ready": True, "model_id": "mock-rules-1.0", "embedding": embedding_status(db, FIXTURE_MODEL_ID)},
    }
    if p.is_demo:
        out["local"] = {"ready": None, "detail": "Not checked for demo sessions."}
        return out
    local = readiness()
    local["embedding"] = embedding_status(db, ollama_model_id())
    local["ready"] = bool(local["ready"] and local["embedding"]["complete"])
    out["local"] = local
    return out


@router.get("/ops/status")
def ops_status(p: Principal = Depends(principal), db: Session = Depends(get_db)) -> dict:
    require(p, "ops.view")
    jobs = dict(db.execute(select(JobLease.status, func.count()).group_by(JobLease.status)).all())
    runs = dict(db.execute(select(Run.status, func.count()).group_by(Run.status)).all())
    latency = {
        mode: {"runs": n, "mean_analysis_s": round(avg, 2) if avg else None, "max_analysis_s": mx}
        for mode, n, avg, mx in db.execute(
            select(Run.mode, func.count(), func.avg(Run.analysis_seconds), func.max(Run.analysis_seconds))
            .where(Run.analysis_seconds.is_not(None))
            .group_by(Run.mode)
        ).all()
    }
    retries = db.scalar(select(func.coalesce(func.sum(JobLease.attempts - 1), 0)).where(JobLease.attempts > 1))
    rejected = db.scalar(select(func.count()).select_from(RunEvent).where(RunEvent.kind == "agent_output_rejected"))
    invalid = db.scalar(
        select(func.count()).select_from(SpecialistAssessment).where(SpecialistAssessment.status == "invalid")
    )
    failures = [
        {"run_id": str(r.id), "code": r.failure_code, "at": r.updated_at.isoformat()}
        for r in db.scalars(select(Run).where(Run.status == "failed").order_by(Run.updated_at.desc()).limit(10))
    ]
    return {
        "jobs": jobs,
        "runs": runs,
        "analysis_latency": latency,
        "job_retries": int(retries or 0),
        "validation_rejections": rejected,
        "invalid_agent_outputs": invalid,
        "recent_failures": failures,
    }


@router.get("/workspaces/{workspace_id}/evidence/search")
def evidence_search(
    workspace_id: uuid.UUID,
    q: str = Query(..., min_length=2, max_length=300),
    role: Literal["water", "air", "ground", "coordinator"] = "coordinator",
    mode: Literal["mock", "local"] = "mock",
    p: Principal = Depends(principal),
    db: Session = Depends(get_db),
) -> dict:
    if p.is_demo:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Evidence search is not available in demo sessions.")
    require_workspace(db, p, workspace_id)
    model_id = ollama_model_id() if mode == "local" else FIXTURE_MODEL_ID
    return retrieve(db, workspace_id, role, q, model_id).as_dict()


@router.post("/runs/{run_id}/library", status_code=201)
def add_report_to_library(run_id: uuid.UUID, p: Principal = Depends(principal), db: Session = Depends(get_db)) -> dict:
    """Add a completed run's report to this workspace's evidence library (workspace-scoped)."""
    require(p, "evidence.manage")
    run = require_run(db, p, run_id)
    rep = db.scalar(select(Report).where(Report.run_id == run.id))
    if rep is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only runs with a completion report can be added.")
    sections = [(f"Prior run {str(run.id)[:8]} summary", rep.markdown[:1800])]
    doc_id = f"prior-report-{run.id}"
    ingest_document(
        db,
        doc_id,
        f"Prior report: {run.inputs.get('title')}",
        "prior_report",
        ["water", "air", "ground", "coordinator"],
        "1",
        "active",
        f"Generated by TerraShield run {run.id} (simulation output, not observation).",
        "Project-internal",
        run.finished_at.date().isoformat() if run.finished_at else "",
        sections,
        workspace_id=run.workspace_id,
    )
    ensure_fixture_model(db)
    embed_chunks(db, FIXTURE_MODEL_ID)
    audit(db, p, "evidence.prior_report_added", "run", run.id, run.workspace_id, document_id=doc_id)
    db.commit()
    return {"document_id": doc_id, "note": "Embedded with the Mock fixture; run a re-embed job for the local model."}


@router.post("/evidence/reembed", status_code=202)
def reembed(p: Principal = Depends(principal), db: Session = Depends(get_db)) -> dict:
    require(p, "evidence.manage")
    job = queue.enqueue(
        db,
        "embed",
        None,
        f"embed:{settings.ollama_embed_model}:{uuid.uuid4().hex[:8]}",
        {"model": settings.ollama_embed_model},
    )
    audit(db, p, "evidence.reembed_requested", "embedding_model", ollama_model_id())
    db.commit()
    return {"job_id": str(job.id), "model_id": ollama_model_id()}
