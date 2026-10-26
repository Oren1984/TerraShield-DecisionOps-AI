"""Job execution: analysis, re-planning, approved-plan simulation and re-embedding."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.agents.workflow import RunCancelled, RunFailed, Workflow
from app.audit import trace
from app.config import settings
from app.jobs.statemachine import transition
from app.logging_setup import log, run_id_var
from app.models import Approval, CandidatePlanRow, JobLease, Report, Run, Scenario, ScenarioVersion, SimulatedEvent
from app.reports.builder import build_report, compare, remaining_risks
from app.simulation.district import load_district
from app.simulation.engine import PlanAction, simulate
from app.simulation.scenario import ScenarioParams

logger = logging.getLogger("terrashield.jobs")


class TerminalJobError(Exception):
    """Failure that retrying will not fix; the run is marked failed immediately."""


def execute(db: Session, job: JobLease, checkpoint: Callable[[], None]) -> None:
    if job.kind == "embed":
        _embed(db, job)
        return
    run = db.get(Run, job.run_id)
    if run is None:
        raise TerminalJobError("run not found")
    token = run_id_var.set(str(run.id))
    try:
        if job.kind in ("analyze", "replan"):
            _analyze(db, run, job.kind, checkpoint)
        elif job.kind == "simulate":
            simulate_approved(db, run, checkpoint)
        else:
            raise TerminalJobError(f"unknown job kind {job.kind}")
    finally:
        run_id_var.reset(token)


def _analyze(db: Session, run: Run, kind: str, checkpoint: Callable[[], None]) -> None:
    wf = Workflow(db, run, checkpoint=checkpoint)
    try:
        if kind == "analyze":
            wf.analyze()
        else:
            if not transition(
                db, run.id, {"queued", "planning"}, "planning", "worker", f"Re-planning for revision {run.revision}."
            ):
                raise RunCancelled()
            wf.plan()
    except RunCancelled:
        db.rollback()
        transition(db, run.id, {"queued", "assessing", "planning"}, "cancelled", "worker", "Run cancelled on request.")
        db.commit()
    except RunFailed as exc:
        db.rollback()
        transition(
            db,
            run.id,
            {"queued", "assessing", "planning"},
            "failed",
            "worker",
            f"Run failed: {exc.code}.",
            failure_code=exc.code,
            failure_detail=exc.detail[:4000],
        )
        db.commit()
        log(logger, logging.WARNING, "run failed", code=exc.code)


def approval_problem(db: Session, run: Run) -> tuple[str | None, Approval | None, CandidatePlanRow | None]:
    """Check that the active approval still binds the current scenario version and plan."""
    approval = db.scalar(
        select(Approval).where(Approval.run_id == run.id, Approval.decision == "approved", Approval.status == "active")
    )
    if approval is None:
        return "No active approval for this run.", None, None
    plan = db.get(CandidatePlanRow, approval.plan_id) if approval.plan_id else None
    scenario = db.get(Scenario, run.scenario_id)
    current = (
        db.scalar(
            select(ScenarioVersion).where(
                ScenarioVersion.scenario_id == run.scenario_id, ScenarioVersion.version == scenario.current_version
            )
        )
        if scenario
        else None
    )
    if plan is None or plan.plan_hash != approval.plan_hash or plan.status != "verified":
        return "The approved plan no longer matches its approved version.", approval, plan
    if (
        current is None
        or current.id != approval.scenario_version_id
        or approval.scenario_version_id != run.scenario_version_id
    ):
        return "The scenario changed after approval; the approval no longer applies.", approval, plan
    if approval.input_hash != run.input_hash:
        return "Run inputs differ from the approved inputs.", approval, plan
    return None, approval, plan


def simulate_approved(db: Session, run: Run, checkpoint: Callable[[], None]) -> None:
    problem, approval, plan = approval_problem(db, run)
    if run.status == "approved" and problem:
        if approval is not None:
            approval.status = "invalidated"
            approval.invalidated_reason = problem
        transition(db, run.id, {"approved"}, "blocked", "policy", problem, blocked_reason=problem)
        db.commit()
        return
    if not transition(db, run.id, {"approved", "simulating"}, "simulating", "worker", "Simulating the approved plan."):
        return
    db.commit()
    assert approval is not None and plan is not None
    district = load_district()
    params = ScenarioParams.model_validate(run.inputs["params"])
    restrictions = {k: int(v) for k, v in (run.evidence or {}).get("restrictions", {}).items()}
    actions = [PlanAction.from_dict(a) for a in plan.actions]
    sim = simulate(district, params, actions, restrictions)
    baseline = simulate(district, params, None, restrictions)
    prev_failed: set[str] = set()
    for hour in sim.hours:
        db.refresh(run)
        if run.cancel_requested:
            transition(
                db, run.id, {"simulating"}, "cancelled", "worker", f"Simulation cancelled at hour {hour['hour']}."
            )
            db.commit()
            return
        failed_now = {a for a, v in hour["assets"].items() if v["state"] == "failed"}
        payload = {
            "disruption": hour["disruption"],
            "baseline_disruption": baseline.hours[hour["hour"]]["disruption"],
            "new_failures": sorted(failed_now - prev_failed),
            "started": [a.key() for a in actions if a.start_h == hour["hour"]],
            "completed": [a.key() for a in actions if a.end_h == hour["hour"]],
            "closed_edges": hour["closed_edges"],
            "states": {a: v["state"] for a, v in hour["assets"].items()},
            "zones": {z: {"depth_m": v["depth_m"], "saturation": v["saturation"]} for z, v in hour["zones"].items()},
            "rain_mm_h": hour["rain_mm_h"],
            "sea_level_m": hour["sea_level_m"],
            "wind_ms": hour["wind_ms"],
        }
        prev_failed = failed_now
        db.execute(
            insert(SimulatedEvent)
            .values(
                run_id=run.id,
                plan_id=plan.id,
                hour=hour["hour"],
                kind="hour",
                payload=payload,
                created_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing()
        )
        db.commit()
        checkpoint()
        if settings.sim_step_delay_ms:
            time.sleep(settings.sim_step_delay_ms / 1000)
    run.outcome = {
        "comparison": compare(baseline.summary, sim.summary),
        "plan_summary": sim.summary,
        "baseline_summary": baseline.summary,
        "remaining_risks": remaining_risks(sim.summary, sim.hours[-1]),
        "final_hour": {
            "closed_edges": sim.hours[-1]["closed_edges"],
            "states": {a: v["state"] for a, v in sim.hours[-1]["assets"].items()},
        },
        "plan_disruption_by_hour": [h["disruption"] for h in sim.hours],
        "baseline_disruption_by_hour": [h["disruption"] for h in baseline.hours],
    }
    approval.status = "consumed"
    if not transition(db, run.id, {"simulating"}, "completed", "worker", "Simulation completed."):
        db.rollback()
        return
    db.flush()
    db.refresh(run)
    content, md = build_report(db, run)
    existing = db.scalar(select(Report).where(Report.run_id == run.id))
    if existing:
        existing.content, existing.markdown = content, md
    else:
        db.add(Report(run_id=run.id, content=content, markdown=md))
    trace(db, run.id, "reports", "report_created", "Completion report generated.")
    db.commit()


def _embed(db: Session, job: JobLease) -> None:
    from app.agents.retrieval import embed_chunks, register_ollama_model

    model = register_ollama_model(db, job.payload.get("model"))
    n = embed_chunks(db, model.id)
    db.commit()
    log(logger, logging.INFO, "re-embedded corpus", model_id=model.id, chunks=n)
