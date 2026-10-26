"""Domain operations shared by the API, CLI and seeding.

Authorization is checked by callers (API layer) before these run; these
functions enforce state and version rules.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.retrieval import FIXTURE_MODEL_ID, ollama_model_id
from app.audit import audit, trace
from app.auth.sessions import Principal
from app.config import settings
from app.jobs import queue
from app.jobs.runner import approval_problem
from app.jobs.statemachine import CANCELLABLE, transition
from app.models import Approval, CandidatePlanRow, JobLease, Run, Scenario, ScenarioVersion
from app.simulation.district import load_district
from app.simulation.scenario import ScenarioParams
from app.versions import PLANNER_VERSION, POLICY_VERSION, SIMULATOR_VERSION, WORKFLOW_VERSION


def conflict(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, detail)


def seeded_templates() -> list[dict[str, Any]]:
    d = Path(settings.scenario_dir) / "seeded"
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.json"))]


def validate_params(params: ScenarioParams) -> None:
    """Domain checks beyond field ranges."""
    district = load_district()
    unknown = [a for a in params.must_keep_operational + params.affected_assets if a not in district.assets]
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown asset ids: {sorted(set(unknown))}")
    bad_zones = [z for z in params.soil_saturation if z not in district.zones]
    if bad_zones:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown zone ids: {bad_zones}")
    for name, hour in (
        ("rainfall.peak_hour", params.rainfall.peak_hour),
        ("coastal.surge_peak_hour", params.coastal.surge_peak_hour),
        ("wind.peak_hour", params.wind.peak_hour),
    ):
        if hour > params.duration_h:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{name} must be within duration_h")


def create_scenario_from_template(
    db: Session, workspace_id: uuid.UUID, tpl: dict[str, Any], created_by: uuid.UUID | None
) -> Scenario:
    params = ScenarioParams.model_validate(tpl["params"])
    sc = Scenario(
        workspace_id=workspace_id,
        slug=tpl["slug"],
        title=tpl["title"],
        summary=tpl["summary"],
        template={k: tpl.get(k) for k in ("provenance", "assumptions", "known_constraints", "expected_behavior")},
        current_version=1,
        created_by=created_by,
    )
    db.add(sc)
    db.flush()
    db.add(
        ScenarioVersion(
            scenario_id=sc.id,
            version=1,
            params=params.model_dump(mode="json"),
            seed=tpl["seed"],
            input_hash=params.input_hash(),
            change_note="Seeded scenario",
            created_by=created_by,
        )
    )
    db.flush()
    return sc


def seed_workspace_scenarios(db: Session, workspace_id: uuid.UUID, created_by: uuid.UUID | None) -> list[Scenario]:
    out = []
    existing = set(db.scalars(select(Scenario.slug).where(Scenario.workspace_id == workspace_id)))
    for tpl in seeded_templates():
        if tpl["slug"] not in existing:
            out.append(create_scenario_from_template(db, workspace_id, tpl, created_by))
    return out


def current_version(db: Session, sc: Scenario) -> ScenarioVersion:
    v = db.scalar(
        select(ScenarioVersion).where(
            ScenarioVersion.scenario_id == sc.id, ScenarioVersion.version == sc.current_version
        )
    )
    assert v is not None
    return v


def update_scenario(
    db: Session,
    p: Principal,
    sc: Scenario,
    params: ScenarioParams,
    seed: int | None,
    change_note: str,
    title: str | None = None,
) -> ScenarioVersion:
    validate_params(params)
    cur = current_version(db, sc)
    new = ScenarioVersion(
        scenario_id=sc.id,
        version=sc.current_version + 1,
        params=params.model_dump(mode="json"),
        seed=seed if seed is not None else cur.seed,
        input_hash=params.input_hash(),
        change_note=change_note[:500],
        created_by=p.user_id,
    )
    db.add(new)
    sc.current_version = new.version
    if title:
        sc.title = title[:160]
    db.flush()
    # Any approval tied to an older version is no longer valid.
    invalidated = []
    for run in db.scalars(select(Run).where(Run.scenario_id == sc.id, Run.status == "approved")):
        for ap in db.scalars(select(Approval).where(Approval.run_id == run.id, Approval.status == "active")):
            ap.status = "invalidated"
            ap.invalidated_reason = f"Scenario edited to version {new.version}."
        reason = f"Scenario changed to version {new.version} after approval; the approval was invalidated."
        transition(db, run.id, {"approved"}, "blocked", "policy", reason, blocked_reason=reason)
        invalidated.append(str(run.id))
    audit(
        db,
        p,
        "scenario.updated",
        "scenario",
        sc.id,
        sc.workspace_id,
        version=new.version,
        invalidated_runs=invalidated,
        change_note=change_note[:200],
    )
    return new


def create_run(
    db: Session,
    p: Principal | None,
    sc: Scenario,
    mode: str,
    seed: int | None = None,
    is_demo_seed: bool = False,
    retry_of: uuid.UUID | None = None,
) -> Run:
    if mode not in ("mock", "local"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "mode must be 'mock' or 'local'")
    ver = current_version(db, sc)
    params = ScenarioParams.model_validate(ver.params)
    run = Run(
        workspace_id=sc.workspace_id,
        scenario_id=sc.id,
        scenario_version_id=ver.id,
        status="draft",
        mode=mode,
        seed=seed if seed is not None else ver.seed,
        simulator_version=SIMULATOR_VERSION,
        planner_version=PLANNER_VERSION,
        policy_version=POLICY_VERSION,
        workflow_version=WORKFLOW_VERSION,
        model_id=f"ollama:{settings.ollama_model}" if mode == "local" else "mock-rules-1.0",
        embedding_model_id=ollama_model_id() if mode == "local" else FIXTURE_MODEL_ID,
        input_hash=ver.input_hash,
        inputs={"params": ver.params, "title": sc.title, "scenario_version": ver.version},
        created_by=p.user_id if p else None,
        is_demo_seed=is_demo_seed,
        retry_of=retry_of,
    )
    db.add(run)
    db.flush()
    trace(
        db,
        run.id,
        p.role if p else "system",
        "run_created",
        f"Run created in {mode} mode.",
        mode=mode,
        seed=run.seed,
        scenario_version=ver.version,
        input_hash=ver.input_hash,
    )
    try:
        validate_params(params)
    except HTTPException as exc:
        transition(
            db,
            run.id,
            {"draft"},
            "failed",
            "validator",
            "Inputs failed validation.",
            failure_code="invalid_inputs",
            failure_detail=str(exc.detail),
        )
        raise
    transition(db, run.id, {"draft"}, "validated", "validator", "Inputs validated.")
    transition(db, run.id, {"validated"}, "queued", "api", "Queued for analysis.")
    queue.enqueue(db, "analyze", run.id, f"run:{run.id}:analyze:0")
    audit(db, p, "run.created", "run", run.id, sc.workspace_id, mode=mode)
    db.refresh(run)
    return run


def review_run(
    db: Session,
    p: Principal,
    run: Run,
    decision: str,
    reason: str,
    plan_id: uuid.UUID | None,
    excluded_actions: list[str] | None = None,
    budget_cap: float | None = None,
    decider_label: str | None = None,
) -> Approval:
    if run.status != "awaiting_review":
        raise conflict(f"Run is {run.status}; only runs awaiting review can be decided.")
    reason = reason.strip()
    if len(reason) < 3:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A reason is required.")
    sc = db.get(Scenario, run.scenario_id)
    ver = current_version(db, sc)  # type: ignore[arg-type]
    plan = None
    if decision == "approved":
        if ver.id != run.scenario_version_id:
            raise conflict("The scenario changed after this run started. Start a new run to approve a plan.")
        plan = db.get(CandidatePlanRow, plan_id) if plan_id else None
        if plan is None or plan.run_id != run.id or plan.revision != run.revision:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found for this run revision.")
        if plan.status != "verified":
            raise conflict("Only plans that passed every verifier check can be approved.")
    ap = Approval(
        run_id=run.id,
        plan_id=plan.id if plan else None,
        scenario_version_id=run.scenario_version_id,
        plan_hash=plan.plan_hash if plan else None,
        input_hash=run.input_hash,
        decision=decision,
        reason=reason[:2000],
        status="active" if decision == "approved" else "closed",
        decided_by=p.user_id,
        decider_label=decider_label or p.username or p.role,
    )
    if decision == "approved":
        if not transition(db, run.id, {"awaiting_review"}, "approved", "owner", f"Owner approved {plan.title}."):  # type: ignore[union-attr]
            raise conflict("Run state changed; reload and try again.")
    elif decision == "rejected":
        if not transition(db, run.id, {"awaiting_review"}, "rejected", "owner", "Owner rejected the plans."):
            raise conflict("Run state changed; reload and try again.")
    elif decision == "revision_requested":
        valid_keys = {f"{a['type']}:{tgt}" for a in _catalog() for tgt in a["valid_targets"]}
        excluded = sorted({k for k in (excluded_actions or []) if k in valid_keys})
        new_rev = run.revision + 1
        if not transition(
            db,
            run.id,
            {"awaiting_review"},
            "queued",
            "owner",
            f"Owner requested revision {new_rev}.",
            revision=new_rev,
            revision_constraints={"excluded_actions": excluded, "budget_cap": budget_cap, "reason": reason[:500]},
        ):
            raise conflict("Run state changed; reload and try again.")
        queue.enqueue(db, "replan", run.id, f"run:{run.id}:replan:{new_rev}")
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown decision.")
    db.add(ap)
    audit(
        db,
        p,
        f"run.{decision}",
        "run",
        run.id,
        run.workspace_id,
        plan_id=str(plan.id) if plan else None,
        scenario_version_id=str(run.scenario_version_id),
        reason=reason[:200],
    )
    db.flush()
    return ap


def _catalog() -> list[dict[str, Any]]:
    from app.simulation.actions import catalog_payload

    return catalog_payload(load_district())


def request_simulation(db: Session, p: Principal, run: Run) -> JobLease:
    if run.status != "approved":
        raise conflict(f"Run is {run.status}; only approved runs can be simulated.")
    problem, approval, _ = approval_problem(db, run)
    if problem:
        if approval is not None:
            approval.status = "invalidated"
            approval.invalidated_reason = problem
        transition(db, run.id, {"approved"}, "blocked", "policy", problem, blocked_reason=problem)
        audit(db, p, "run.simulation_blocked", "run", run.id, run.workspace_id, reason=problem)
        db.commit()
        raise conflict(problem)
    trace(db, run.id, p.role, "simulation_requested", "Simulation of the approved plan requested.")
    audit(db, p, "run.simulation_requested", "run", run.id, run.workspace_id)
    return queue.enqueue(db, "simulate", run.id, f"run:{run.id}:simulate")


def cancel_run(db: Session, p: Principal, run: Run) -> str:
    if run.status not in CANCELLABLE:
        raise conflict(f"Run is {run.status} and cannot be cancelled.")
    if transition(db, run.id, {"queued", "awaiting_review", "approved"}, "cancelled", p.role, "Run cancelled."):
        for job in db.scalars(select(JobLease).where(JobLease.run_id == run.id, JobLease.status == "pending")):
            job.status = "cancelled"
        for ap in db.scalars(select(Approval).where(Approval.run_id == run.id, Approval.status == "active")):
            ap.status = "invalidated"
            ap.invalidated_reason = "Run cancelled."
        result = "cancelled"
    else:
        run.cancel_requested = True
        trace(db, run.id, p.role, "cancel_requested", "Cancellation requested; the worker stops at the next step.")
        result = "cancel_requested"
    audit(db, p, "run.cancel", "run", run.id, run.workspace_id, result=result)
    return result
