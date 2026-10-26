"""Evaluation harness (run with ``python -m app.cli evaluate-planning`` / ``evaluate-retrieval``).

Planning evaluation uses *unseen* scenario variants: seeded perturbations of the
four seeded scenarios drawn with an evaluation seed that was not used while the
scenarios were tuned. It compares:

* ``baseline``: no action
* ``greedy``: benefit-per-cost heuristic over the full catalog
* ``constrained``: CP-SAT over the full catalog (deterministic planner)
* ``agent_team``: Mock specialists + coordinator (restrictions, conflicts, one revision round)
* ``single_coordinator``: one Mock coordinator proposing for every failing asset,
  with no specialist decomposition, no restrictions and no revision round

Every accepted plan is re-checked by the independent verifier; the release gate
is zero accepted hard-constraint violations.
"""

from __future__ import annotations

import json
import random
import statistics
import time
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.agents.conflicts import accepted_restrictions, detect_conflicts
from app.agents.evidence import build_evidence, evidence_for_role
from app.agents.providers import MockProvider
from app.agents.schemas import ROLE_ACTIONS, CoordinatorConsolidation, SpecialistAssessment
from app.agents.validate import ValidationContext, validate_consolidation, validate_specialist
from app.config import settings
from app.planning.planner import CandidatePlan, plan_scenario
from app.planning.verifier import verify_plan
from app.simulation.actions import CATALOG, valid_targets
from app.simulation.district import District, load_district
from app.simulation.engine import simulate
from app.simulation.scenario import ScenarioParams
from app.simulation.sensitivity import perturbed

EVAL_SEED = 97_531


def seeded_params() -> list[tuple[str, ScenarioParams]]:
    d = Path(settings.scenario_dir) / "seeded"
    out = []
    for p in sorted(d.glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        out.append((data["slug"], ScenarioParams.model_validate(data["params"])))
    return out


def variants(n_per: int, seed: int = EVAL_SEED) -> list[tuple[str, ScenarioParams]]:
    rng = random.Random(seed)
    district = load_district()
    out = []
    for slug, p in seeded_params():
        for i in range(n_per):
            out.append((f"{slug}#{i}", perturbed(p, rng, district)))
    return out


def _payload(district: District, params: ScenarioParams, evidence: list[dict[str, Any]], role: str) -> dict[str, Any]:
    return {
        "role": role,
        "evidence": evidence_for_role(evidence, role),
        "allowed_actions": [
            {"type": t, "valid_targets": valid_targets(district, t)} for t in sorted(ROLE_ACTIONS[role])
        ],
        "resources": params.resources.model_dump(),
        "criticality": {a.id: a.criticality for a in district.assets.values()},
        "zones": [
            {"id": z.id, "name": z.name, "downstream": [t for t, _ in z.downstream]} for z in district.zones.values()
        ],
        "guidance": [],
        "scenario_notes_untrusted": params.notes,
    }


def agent_team_candidates(
    district: District, params: ScenarioParams, seed: int
) -> tuple[list[str], dict[str, int], int]:
    """Mock team workflow without the database. Returns candidate keys, restrictions, invalid outputs."""
    sim = simulate(district, params)
    ev = build_evidence(district, params, sim)
    ctx = ValidationContext(district, params, ev)
    prov = MockProvider(seed)
    invalid = 0
    payloads = {r: _payload(district, params, ev, r) for r in ("water", "air", "ground")}
    team: dict[str, SpecialistAssessment] = {}
    for role, pl in payloads.items():
        out, errs = validate_specialist(prov.generate(role, "assess", pl, SpecialistAssessment).raw, role, ctx)
        invalid += bool(errs)
        if out:
            team[role] = out

    def consolidate(allow: bool) -> tuple[CoordinatorConsolidation | None, list[dict[str, Any]]]:
        nonlocal invalid
        conflicts = detect_conflicts(district, params, sim, team)
        ctx.conflict_ids = {c["id"] for c in conflicts}
        proposals = [dict(p.model_dump(), role=r) for r, a in team.items() for p in a.proposed_actions]
        ctx.proposed_keys = {f"{p['action_type']}:{p['target']}" for p in proposals}
        raw = prov.generate(
            "coordinator",
            "consolidate",
            {"evidence": ev, "conflicts": conflicts, "proposals": proposals, "allow_revisions": allow, "guidance": []},
            CoordinatorConsolidation,
        ).raw
        out, errs = validate_consolidation(raw, ctx)
        invalid += bool(errs)
        return out, conflicts

    cons, conflicts = consolidate(True)
    if cons and cons.revision_requests:
        by_id = {c["id"]: c for c in conflicts}
        for role in {r.role for r in cons.revision_requests}:
            reqs = [
                {"conflict_id": r.conflict_id, "action": by_id[r.conflict_id].get("action")}
                for r in cons.revision_requests
                if r.role == role
            ]
            pl = dict(payloads[role], previous=team[role].model_dump(), revision_requests=reqs)
            out, errs = validate_specialist(prov.generate(role, "revise", pl, SpecialistAssessment).raw, role, ctx)
            invalid += bool(errs)
            if out:
                team[role] = out
        cons, _ = consolidate(False)
    keys = [f"{s.action_type}:{s.target}" for s in cons.selected_actions] if cons else []
    return keys, accepted_restrictions(team), invalid


def single_coordinator_candidates(district: District, params: ScenarioParams) -> list[str]:
    """One generalist role: first applicable action per failing asset, no restrictions or revisions."""
    sim = simulate(district, params)
    keys: list[str] = []
    for aid in sim.summary["failed_assets"]:
        for t in CATALOG:
            if t in ("secure_mast", "brace_structure") or aid not in valid_targets(district, t):
                continue
            keys.append(f"{t}:{aid}")
            break
    for aid in params.must_keep_operational:
        a = district.assets[aid]
        if a.powered_by and f"backup_generator:{aid}" not in keys:
            keys.append(f"backup_generator:{aid}")
    return keys


def _best(plans: list[CandidatePlan], labels: set[str]) -> CandidatePlan | None:
    ok = [p for p in plans if p.label in labels and p.status == "verified"]
    return min(ok, key=lambda p: p.objective()["score"]) if ok else None


def evaluate_planning(n_per: int = 5, seed: int = EVAL_SEED) -> dict[str, Any]:
    district = load_district()
    methods = ["baseline", "greedy", "constrained", "agent_team", "single_coordinator"]
    rows: list[dict[str, Any]] = []
    violations = 0
    invalid_outputs = 0
    timings: dict[str, list[float]] = {m: [] for m in methods}
    cp = ["cpsat_balanced", "cpsat_low_cost", "cpsat_max_protection"]
    for name, params in variants(n_per, seed):
        row: dict[str, Any] = {"variant": name, "has_requirements": bool(params.must_keep_operational)}
        t0 = time.perf_counter()
        full = plan_scenario(district, params, seed, with_sensitivity=False)
        timings["constrained"].append(time.perf_counter() - t0)
        base = full.baseline.summary["disruption_total"]
        row["baseline"] = {
            "disruption": base,
            "valid": next(p for p in full.plans if p.label == "baseline").status == "verified",
        }
        for method, plans, labels in [
            ("greedy", full.plans, {"greedy"}),
            ("constrained", full.plans, set(cp)),
        ]:
            b = _best(plans, labels)
            row[method] = {"disruption": b.objective()["disruption_total"] if b else None, "valid": b is not None}
        t0 = time.perf_counter()
        keys, restr, inv = agent_team_candidates(district, params, seed)
        invalid_outputs += inv
        team = plan_scenario(district, params, seed, candidate_keys=keys, restrictions=restr, with_sensitivity=False)
        timings["agent_team"].append(time.perf_counter() - t0)
        b = _best(team.plans, set(cp))
        row["agent_team"] = {
            "disruption": b.objective()["disruption_total"] if b else None,
            "valid": b is not None,
            "restrictions": len(restr),
            "candidates": len(keys),
        }
        t0 = time.perf_counter()
        single = plan_scenario(
            district,
            params,
            seed,
            candidate_keys=single_coordinator_candidates(district, params),
            with_sensitivity=False,
        )
        timings["single_coordinator"].append(time.perf_counter() - t0)
        b = _best(single.plans, set(cp))
        row["single_coordinator"] = {
            "disruption": b.objective()["disruption_total"] if b else None,
            "valid": b is not None,
        }
        # Independent re-verification of every plan any method would offer for approval.
        for outcome, r in ((full, {}), (team, restr), (single, {})):
            for p in outcome.plans:
                if p.status == "verified" and not verify_plan(district, params, p.actions, r).ok:
                    violations += 1
        rows.append(row)

    def summary(method: str) -> dict[str, Any]:
        vals = [r[method] for r in rows]
        valid = [v for v in vals if v["valid"]]
        reductions = [
            100 * (r["baseline"]["disruption"] - r[method]["disruption"]) / r["baseline"]["disruption"]
            for r in rows
            if r[method]["valid"] and r[method]["disruption"] is not None and r["baseline"]["disruption"]
        ]
        return {
            "feasible_plan_rate": round(len(valid) / len(vals), 3),
            "mean_disruption_when_feasible": round(statistics.mean(v["disruption"] for v in valid), 2)
            if valid
            else None,
            "mean_reduction_vs_no_action_pct": round(statistics.mean(reductions), 1) if reductions else None,
            "mean_runtime_s": round(statistics.mean(timings[method]), 3) if timings[method] else None,
        }

    paired = [r for r in rows if r["agent_team"]["valid"] and r["constrained"]["valid"]]
    return {
        "eval_seed": seed,
        "variants": len(rows),
        "per_scenario": n_per,
        "methods": {m: summary(m) for m in methods},
        "accepted_hard_constraint_violations": violations,
        "invalid_mock_outputs": invalid_outputs,
        "agent_vs_constrained_mean_gap": round(
            statistics.mean(r["agent_team"]["disruption"] - r["constrained"]["disruption"] for r in paired), 2
        )
        if paired
        else None,
        "agent_vs_single_wins": sum(
            1
            for r in rows
            if r["agent_team"]["valid"]
            and (
                not r["single_coordinator"]["valid"]
                or r["agent_team"]["disruption"] < r["single_coordinator"]["disruption"]
            )
        ),
        "single_vs_agent_wins": sum(
            1
            for r in rows
            if r["single_coordinator"]["valid"]
            and (not r["agent_team"]["valid"] or r["single_coordinator"]["disruption"] < r["agent_team"]["disruption"])
        ),
        "rows": rows,
    }


def evaluate_retrieval(db: Session, model_id: str) -> dict[str, Any]:
    from app.agents.retrieval import embed_chunks, ingest_document, retrieve
    from app.models import Document, Workspace

    data = json.loads((Path(settings.scenario_dir) / "eval" / "retrieval_questions.json").read_text(encoding="utf-8"))
    ws_a = Workspace(name=f"eval-a-{uuid.uuid4().hex[:6]}")
    ws_b = Workspace(name=f"eval-b-{uuid.uuid4().hex[:6]}")
    db.add_all([ws_a, ws_b])
    db.flush()
    try:
        hits, rr, top1 = 0, 0.0, 0
        details = []
        for item in data["relevant"]:
            res = retrieve(db, ws_a.id, item["role"], item["q"], model_id)
            docs = [c.document_id for c in res.chunks]
            rank = next((i + 1 for i, d in enumerate(docs) if d in item["expect"]), None)
            hits += rank is not None
            rr += 1 / rank if rank else 0
            top1 += rank == 1
            details.append(
                {
                    "q": item["q"],
                    "rank": rank,
                    "top": docs[:2],
                    "sims": [c.similarity for c in res.chunks[:2]],
                    "status": res.status,
                }
            )
        irrelevant = [retrieve(db, ws_a.id, i["role"], i["q"], model_id) for i in data["irrelevant"]]
        stale = [retrieve(db, ws_a.id, s["role"], s["q"], model_id, k=10) for s in data["stale"]]
        stale_ok = all(
            all(c.document_id != s["superseded"] for c in r.chunks) for r, s in zip(stale, data["stale"], strict=True)
        )
        conflicting = [retrieve(db, ws_a.id, c["role"], c["q"], model_id, k=6) for c in data["conflicting"]]
        conflict_ok = all(
            set(c["expect_all"]) <= {x.document_id for x in r.chunks}
            for r, c in zip(conflicting, data["conflicting"], strict=True)
        )
        # Cross-workspace denial: a prior report in workspace B must never reach workspace A.
        doc_id = f"eval-prior-{uuid.uuid4().hex[:6]}"
        ingest_document(
            db,
            doc_id,
            "Prior report: storm surge pump station decision",
            "prior_report",
            ["water", "air", "ground", "coordinator"],
            "1",
            "active",
            "evaluation fixture",
            "n/a",
            "",
            [("Summary", "Prior run approved a flood barrier at the Quay pump station before the surge peak.")],
            workspace_id=ws_b.id,
        )
        embed_chunks(db, model_id)
        q = "prior run flood barrier at the Quay pump station before the surge"
        in_b = any(c.document_id == doc_id for c in retrieve(db, ws_b.id, "water", q, model_id, k=10).chunks)
        in_a = any(c.document_id == doc_id for c in retrieve(db, ws_a.id, "water", q, model_id, k=10).chunks)
        n = len(data["relevant"])
        return {
            "model_id": model_id,
            "questions": n,
            "recall_at_k": round(hits / n, 3),
            "mrr": round(rr / n, 3),
            "top1_accuracy": round(top1 / n, 3),
            "k": settings.retrieval_top_k,
            "irrelevant_insufficient_rate": round(
                sum(r.status == "insufficient_evidence" for r in irrelevant) / len(irrelevant), 3
            ),
            "irrelevant_max_similarity": [max((c.similarity for c in r.chunks), default=None) for r in irrelevant],
            "stale_source_excluded": stale_ok,
            "conflicting_sources_both_retrieved": conflict_ok,
            "cross_workspace_denied": in_b and not in_a,
            "details": details,
        }
    finally:
        db.rollback()
        db.query(Document).filter(Document.id.like("eval-prior-%")).delete(synchronize_session=False)
        db.commit()
