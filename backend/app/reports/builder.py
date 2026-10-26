"""Completion report: simulated outcome, remaining risks and the decision record."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Approval,
    CandidatePlanRow,
    Run,
    Scenario,
    ScenarioVersion,
    SpecialistAssessment,
    User,
)
from app.simulation.district import load_district

DISCLAIMER = (
    "Simulation proof of concept. All values come from a simplified, uncalibrated model of a fictional "
    "district and are demonstrative. This report is not a forecast, an emergency instruction, or a "
    "certification, and it controls no real infrastructure."
)


def compare(baseline: dict[str, Any], plan_summary: dict[str, Any]) -> dict[str, Any]:
    b_fail = set(baseline["failed_assets"])
    p_fail = set(plan_summary["failed_assets"])
    b_d = baseline["disruption_total"]
    p_d = plan_summary["disruption_total"]
    return {
        "baseline_disruption": b_d,
        "plan_disruption": p_d,
        "disruption_reduction": round(b_d - p_d, 3),
        "disruption_reduction_pct": round(100 * (b_d - p_d) / b_d, 1) if b_d else 0.0,
        "failures_avoided": sorted(b_fail - p_fail),
        "failures_remaining": sorted(p_fail),
        "new_failures": sorted(p_fail - b_fail),
    }


def remaining_risks(plan_summary: dict[str, Any], last_hour: dict[str, Any]) -> list[dict[str, Any]]:
    d = load_district()
    risks = []
    for aid, info in plan_summary["failed_assets"].items():
        risks.append(
            {
                "kind": "asset_failed",
                "asset": aid,
                "name": d.assets[aid].name,
                "detail": f"Fails at hour {info['hour']} ({info['cause']}) even with the plan.",
            }
        )
    for aid, state in plan_summary["final_states"].items():
        if state in ("unpowered", "degraded") and aid not in plan_summary["failed_assets"]:
            risks.append(
                {
                    "kind": f"asset_{state}",
                    "asset": aid,
                    "name": d.assets[aid].name,
                    "detail": f"Ends the simulation {state}.",
                }
            )
    for eid, cause in sorted(last_hour.get("closed_edges", {}).items()):
        risks.append(
            {
                "kind": "road_closed",
                "edge": eid,
                "name": d.edges[eid].name,
                "detail": f"Still closed at the end of the simulation ({cause}).",
            }
        )
    for aid, r in plan_summary["requirements"].items():
        if not r["satisfied"]:
            risks.append(
                {
                    "kind": "requirement_unmet",
                    "asset": aid,
                    "name": d.assets[aid].name,
                    "detail": f"Requirement violated from hour {r['first_violation_hour']}.",
                }
            )
    return risks


def build_report(db: Session, run: Run) -> tuple[dict[str, Any], str]:
    scenario = db.get(Scenario, run.scenario_id)
    version = db.get(ScenarioVersion, run.scenario_version_id)
    approval = db.scalar(
        select(Approval)
        .where(Approval.run_id == run.id, Approval.decision == "approved")
        .order_by(Approval.decided_at.desc())
    )
    plan = db.get(CandidatePlanRow, approval.plan_id) if approval and approval.plan_id else None
    approver = db.get(User, approval.decided_by) if approval and approval.decided_by else None
    assessments = list(
        db.scalars(
            select(SpecialistAssessment)
            .where(SpecialistAssessment.run_id == run.id, SpecialistAssessment.status == "valid")
            .order_by(SpecialistAssessment.created_at)
        )
    )
    outcome = run.outcome or {}
    explanation = (run.evidence or {}).get("explanation", {})
    citations = sorted({c for a in assessments for c in (a.output or {}).get("citations", [])})
    agents = {}
    for a in assessments:
        agents[a.role] = {
            "step": a.step,
            "round": a.round,
            "provider": a.provider,
            "model_id": a.model_id,
            "summary": (a.output or {}).get("summary") or (a.output or {}).get("explanation", ""),
        }
    content = {
        "disclaimer": DISCLAIMER,
        "run_id": str(run.id),
        "status": run.status,
        "scenario": {
            "title": scenario.title if scenario else "",
            "version": version.version if version else None,
            "input_hash": run.input_hash,
            "seed": run.seed,
        },
        "provenance": {
            "mode": run.mode,
            "model_id": run.model_id,
            "embedding_model_id": run.embedding_model_id,
            "simulator_version": run.simulator_version,
            "planner_version": run.planner_version,
            "policy_version": run.policy_version,
            "workflow_version": run.workflow_version,
        },
        "decision": {
            "plan_label": plan.label if plan else None,
            "plan_title": plan.title if plan else None,
            "plan_hash": approval.plan_hash if approval else None,
            "approved_by": approver.username if approver else (approval.decider_label if approval else None),
            "approved_at": approval.decided_at.isoformat() if approval else None,
            "reason": approval.reason if approval else None,
            "scenario_version_id": str(approval.scenario_version_id) if approval else None,
        },
        "actions": plan.actions if plan else [],
        "objective": plan.objective if plan else None,
        "sensitivity": plan.sensitivity if plan else None,
        "comparison": outcome.get("comparison"),
        "remaining_risks": outcome.get("remaining_risks", []),
        "coordinator_explanation": explanation,
        "agents": agents,
        "citations": citations,
        "timing": {
            "created_at": run.created_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "analysis_seconds": run.analysis_seconds,
        },
    }
    return content, to_markdown(content)


def to_markdown(c: dict[str, Any]) -> str:
    lines = [
        f"# TerraShield simulation report: {c['scenario']['title']}",
        "",
        f"> {c['disclaimer']}",
        "",
        f"- Run: `{c['run_id']}` ({c['status']})",
        f"- Scenario version {c['scenario']['version']}, seed {c['scenario']['seed']}, input hash `{c['scenario']['input_hash'][:16]}`",
        f"- Mode: **{c['provenance']['mode']}**; model: {c['provenance']['model_id'] or 'n/a'}; "
        f"embeddings: {c['provenance']['embedding_model_id'] or 'n/a'}",
        f"- Versions: {c['provenance']['simulator_version']}, {c['provenance']['planner_version']}, "
        f"{c['provenance']['policy_version']}, {c['provenance']['workflow_version']}",
        "",
        "## Decision record",
        "",
    ]
    d = c["decision"]
    if d["plan_label"]:
        lines += [
            f"- Approved plan: **{d['plan_title']}** (`{d['plan_label']}`, hash `{(d['plan_hash'] or '')[:16]}`)",
            f"- Approved by {d['approved_by']} at {d['approved_at']}",
            f"- Reason: {d['reason']}",
        ]
    else:
        lines.append("- No approved plan.")
    lines += ["", "## Actions", ""]
    if c["actions"]:
        lines += ["| Action | Target | Start (h) | End (h) |", "| --- | --- | --- | --- |"]
        lines += [f"| {a['label']} | {a['target']} | {a['start_h']} | {a['end_h']} |" for a in c["actions"]]
    else:
        lines.append("No actions (no-action plan).")
    cmp = c.get("comparison")
    if cmp:
        lines += [
            "",
            "## Simulated outcome versus no action (same scenario and seed)",
            "",
            f"- Disruption indicator: {cmp['plan_disruption']:.1f} with the plan vs {cmp['baseline_disruption']:.1f} without "
            f"({cmp['disruption_reduction_pct']:.1f}% lower)",
            f"- Failures avoided: {', '.join(cmp['failures_avoided']) or 'none'}",
            f"- Failures remaining: {', '.join(cmp['failures_remaining']) or 'none'}",
        ]
    s = c.get("sensitivity")
    if s:
        lines.append(
            f"- Scenario sensitivity (seeded perturbations, not a probability): disruption {s['disruption_p10']}-"
            f"{s['disruption_p90']} (p10-p90, {s['samples']} samples)"
        )
    lines += ["", "## Remaining risks", ""]
    lines += [f"- {r['name']}: {r['detail']}" for r in c["remaining_risks"]] or ["- None identified by the simulator."]
    exp = c.get("coordinator_explanation") or {}
    if exp:
        lines += ["", "## Coordinator explanation", "", exp.get("explanation", "")]
    lines += ["", "## Agent contributions", ""]
    for role, a in c["agents"].items():
        lines.append(f"- **{role}** ({a['provider']}, {a['model_id']}): {a['summary']}")
    if c["citations"]:
        lines += ["", "## Evidence citations", ""] + [f"- `{x}`" for x in c["citations"]]
    lines.append("")
    return "\n".join(lines)
