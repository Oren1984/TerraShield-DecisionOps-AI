"""Planning pipeline: no-action baseline, greedy heuristic and constrained CP-SAT plans.

Every candidate is re-simulated and checked by the independent verifier. The
ranking objective is documented in ``RANKING`` and its components are reported
separately; hard constraints are never traded for score.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from app.config import settings
from app.planning.solver import Option, Problem, solve
from app.planning.verifier import Verification, verify_plan
from app.simulation.actions import (
    CATALOG,
    EQUIPMENT_KINDS,
    MUTUALLY_EXCLUSIVE,
    PREREQUISITES,
    action_location,
    site_wind_exposure,
    valid_targets,
)
from app.simulation.district import District
from app.simulation.engine import PlanAction, SimResult, simulate
from app.simulation.scenario import ScenarioParams
from app.simulation.sensitivity import sensitivity

DISRUPTION_WEIGHT = 1.0
COST_WEIGHT_PER_UNIT = 1 / 2000
COMPLETION_WEIGHT_PER_H = 0.5
RANKING: dict[str, float | str] = {
    "disruption_weight": DISRUPTION_WEIGHT,
    "cost_weight_per_unit": COST_WEIGHT_PER_UNIT,
    "completion_weight_per_h": COMPLETION_WEIGHT_PER_H,
    "description": "score = disruption_total + cost / 2000 + 0.5 * completion_hour (lower is better); "
    "only plans that pass every verifier check are ranked",
}

# (label, title, cost weight in benefit units per currency unit, share of budget usable)
VARIANTS = [
    ("cpsat_balanced", "Constrained plan: balanced", 1 / 2000, 1.0),
    ("cpsat_low_cost", "Constrained plan: lowest cost", 1 / 500, 0.5),
    ("cpsat_max_protection", "Constrained plan: maximum protection", 1 / 50000, 1.0),
]


@dataclass
class CandidatePlan:
    label: str
    title: str
    method: str
    solver_status: str
    actions: list[PlanAction]
    verification: Verification
    iteration: int = 0
    notes: list[str] = field(default_factory=list)
    sensitivity: dict[str, Any] | None = None

    @property
    def cost(self) -> float:
        return sum(CATALOG[a.type].cost for a in self.actions)

    @property
    def completion_h(self) -> int:
        return max((a.end_h for a in self.actions), default=0)

    def objective(self) -> dict[str, Any]:
        sim = self.verification.sim
        disruption = sim.summary["disruption_total"] if sim else None
        if disruption is None:
            return {"disruption_total": None, "cost": self.cost, "completion_h": self.completion_h, "score": None}
        score = (
            DISRUPTION_WEIGHT * disruption
            + COST_WEIGHT_PER_UNIT * self.cost
            + COMPLETION_WEIGHT_PER_H * self.completion_h
        )
        return {
            "disruption_total": disruption,
            "cost": self.cost,
            "completion_h": self.completion_h,
            "score": round(score, 3),
        }

    def plan_hash(self) -> str:
        payload = json.dumps(sorted((a.type, a.target, a.start_h) for a in self.actions))
        return hashlib.sha256(payload.encode()).hexdigest()

    @property
    def status(self) -> str:
        if self.solver_status == "infeasible":
            return "infeasible"
        return "verified" if self.verification.ok else "rejected_by_verifier"


@dataclass
class PlanningOutcome:
    baseline: SimResult
    plans: list[CandidatePlan]
    candidate_keys: list[str]
    excluded_keys: list[str]
    requirement_clauses: list[dict[str, Any]]
    iterations: int
    blocked: bool
    blocked_reason: str | None
    events: list[dict[str, Any]]

    def ranked(self) -> list[CandidatePlan]:
        valid = [p for p in self.plans if p.status == "verified"]
        return sorted(valid, key=lambda p: p.objective()["score"])


class _SingleSims:
    """Cache of one-action simulations used for benefit estimates."""

    def __init__(self, district: District, params: ScenarioParams, restrictions: dict[str, int]):
        self.district = district
        self.params = params
        self.restrictions = restrictions
        self.cache: dict[tuple[PlanAction, ...], SimResult] = {}

    def run(self, plan: tuple[PlanAction, ...]) -> SimResult:
        if plan not in self.cache:
            self.cache[plan] = simulate(self.district, self.params, list(plan), self.restrictions)
        return self.cache[plan]


def _wind_ok(district: District, hazards: dict[str, list[float]], a: PlanAction, horizon: int) -> bool:
    limit = CATALOG[a.type].wind_limit_ms
    exp = site_wind_exposure(district, a.type, a.target)
    return all(hazards["wind_ms"][h] * exp <= limit for h in range(a.start_h, min(a.end_h, horizon)))


def _never_failed(sim: SimResult, aid: str) -> bool:
    return all(h["assets"][aid]["state"] != "failed" for h in sim.hours)


def plan_scenario(
    district: District,
    params: ScenarioParams,
    seed: int,
    candidate_keys: list[str] | None = None,
    excluded_keys: list[str] | None = None,
    budget_cap: float | None = None,
    with_sensitivity: bool = True,
    restrictions: dict[str, int] | None = None,
) -> PlanningOutcome:
    events: list[dict[str, Any]] = []
    excluded = sorted(set(excluded_keys or []))
    work = params.model_copy(deep=True)
    if budget_cap is not None:
        work.resources.budget = min(work.resources.budget, budget_cap)
    res = work.resources

    all_keys = [f"{t}:{tgt}" for t in CATALOG for tgt in valid_targets(district, t)]
    keys = [k for k in (candidate_keys if candidate_keys is not None else all_keys) if k in all_keys]
    # Prerequisite actions are added automatically so a proposed dependent action stays schedulable.
    for k in list(keys):
        t, tgt = k.split(":", 1)
        pre = PREREQUISITES.get(t)
        if pre and f"{pre}:{tgt}" not in keys:
            keys.append(f"{pre}:{tgt}")
    keys = [
        k
        for k in keys
        if k not in excluded and all(getattr(res, e) >= n for e, n in CATALOG[k.split(":")[0]].equipment.items())
    ]

    restrictions = dict(restrictions or {})
    baseline = simulate(district, work, None, restrictions)
    sims = _SingleSims(district, work, restrictions)
    base_d = baseline.summary["disruption_total"]

    unreachable: set[tuple[str, int]] = set()
    for h, hour in enumerate(baseline.hours):
        for j in district.junctions:
            if j not in hour["reachable_junctions"]:
                unreachable.add((j, h))

    def build_options() -> list[Option]:
        opts: list[Option] = []
        for k in keys:
            t, tgt = k.split(":", 1)
            spec = CATALOG[t]
            loc = action_location(district, t, tgt)
            for s in range(res.mobilization_h, work.duration_h - spec.duration_h + 1):
                a = PlanAction(t, tgt, s)
                if (loc, s) in unreachable or not _wind_ok(district, baseline.hazards, a, work.duration_h):
                    continue
                pre = PREREQUISITES.get(t)
                if pre:
                    ps = s - CATALOG[pre].duration_h
                    if ps < res.mobilization_h:
                        continue
                    pa = PlanAction(pre, tgt, ps)
                    ref = sims.run((pa,))
                    with_a = sims.run((pa, a))
                    benefit = ref.summary["disruption_total"] - with_a.summary["disruption_total"]
                else:
                    benefit = base_d - sims.run((a,)).summary["disruption_total"]
                opts.append(Option(k, t, tgt, s, a.end_h, spec.crews, dict(spec.equipment), spec.cost, benefit))
        return opts

    def single_sim_for(o: Option) -> SimResult:
        a = PlanAction(o.type, o.target, o.start)
        pre = PREREQUISITES.get(o.type)
        if pre:
            return sims.run((PlanAction(pre, o.target, o.start - CATALOG[pre].duration_h), a))
        return sims.run((a,))

    def coverage(opts: list[Option]) -> tuple[list[list[int]], list[dict[str, Any]]]:
        clauses: list[list[int]] = []
        info: list[dict[str, Any]] = []
        failed = baseline.summary["failed_assets"]
        for aid in work.must_keep_operational:
            asset = district.assets[aid]
            if aid in failed:
                fix = [i for i, o in enumerate(opts) if _never_failed(single_sim_for(o), aid)]
                info.append({"asset": aid, "threat": f"{failed[aid]['cause']} damage", "single_action_fixes": len(fix)})
                if fix:
                    clauses.append(fix)
            if asset.powered_by and asset.powered_by in failed:
                sup = asset.powered_by
                fail_h = failed[sup]["hour"]
                fix = [
                    i
                    for i, o in enumerate(opts)
                    if _never_failed(single_sim_for(o), sup)
                    or (o.type == "backup_generator" and o.target == aid and o.end <= fail_h)
                ]
                info.append({"asset": aid, "threat": f"loss of supply from {sup}", "single_action_fixes": len(fix)})
                if fix:
                    clauses.append(fix)
        return clauses, info

    plans: dict[str, CandidatePlan] = {}
    clause_info: list[dict[str, Any]] = []
    iterations = 0
    for it in range(1, settings.max_replan_iterations + 1):
        iterations = it
        options = build_options()
        clauses, clause_info = coverage(options)
        events.append({"kind": "planner_iteration", "iteration": it, "options": len(options), "clauses": clause_info})
        new_unreachable = False
        for label, title, cost_w, budget_share in VARIANTS:
            prob = Problem(
                options=options,
                horizon=work.duration_h,
                crews=res.crews,
                equipment={e: getattr(res, e) for e in EQUIPMENT_KINDS},
                budget=res.budget * budget_share,
                exclusive_groups=[
                    [f"{t1}:{tgt}", f"{t2}:{tgt}"]
                    for t1, t2 in MUTUALLY_EXCLUSIVE
                    for tgt in valid_targets(district, t1)
                ],
                prerequisites=[
                    (k, f"{PREREQUISITES[k.split(':')[0]]}:{k.split(':', 1)[1]}")
                    for k in keys
                    if k.split(":")[0] in PREREQUISITES
                ],
                coverage=clauses,
                cost_weight=cost_w,
            )
            sol = solve(prob, time_limit_s=settings.solver_time_limit_s)
            actions = [PlanAction(options[i].type, options[i].target, options[i].start) for i in sol.chosen]
            actions.sort(key=lambda a: (a.start_h, a.type, a.target))
            ver = verify_plan(district, params_with_budget(params, res.budget), actions, restrictions)
            cand = CandidatePlan(label, title, "cp-sat", sol.status, actions, ver, it)
            if sol.status == "time_limited":
                cand.notes.append("Solver stopped at its time limit; plan is feasible but not proven optimal.")
            if sol.status == "infeasible":
                cand.notes.append("No assignment satisfies the hard constraints and requirement clauses.")
            events.append(
                {
                    "kind": "solver_result",
                    "iteration": it,
                    "label": label,
                    "status": sol.status,
                    "actions": len(actions),
                    "wall_s": round(sol.wall_s, 3),
                    "verified": ver.ok,
                }
            )
            prev = plans.get(label)
            if prev is None or (not prev.verification.ok and ver.ok) or it == 1:
                plans[label] = cand
            # Simulate-and-replan: learn closures from this plan's own simulation.
            if ver.sim is not None:
                for h, hour in enumerate(ver.sim.hours):
                    for j in district.junctions:
                        if j not in hour["reachable_junctions"] and (j, h) not in unreachable:
                            unreachable.add((j, h))
                            new_unreachable = True
        access_failures = [
            p
            for p in plans.values()
            if any(c.constraint == "road_access" and not c.passed for c in p.verification.checks)
        ]
        if not access_failures or not new_unreachable:
            break

    greedy = _greedy(district, work, build_options())
    plans["greedy"] = CandidatePlan(
        "greedy",
        "Greedy heuristic",
        "greedy",
        "heuristic",
        greedy,
        verify_plan(district, params_with_budget(params, res.budget), greedy, restrictions),
    )
    plans["baseline"] = CandidatePlan(
        "baseline",
        "No action (baseline)",
        "baseline",
        "reference",
        [],
        verify_plan(district, params_with_budget(params, res.budget), [], restrictions),
    )

    # Deduplicate plans with the same actions and the same simulated outcome, keeping the first label.
    unique: dict[tuple[Any, ...], CandidatePlan] = {}
    for label in ["baseline"] + [v[0] for v in VARIANTS] + ["greedy"]:
        p = plans.get(label)
        if p is None:
            continue
        sig = (frozenset(a.key() for a in p.actions), p.objective()["disruption_total"])
        if sig in unique:
            unique[sig].notes.append(f"Equivalent to {p.title.lower()} (same actions and simulated outcome).")
            continue
        unique[sig] = p
    ordered = list(unique.values())

    if with_sensitivity:
        for p in ordered:
            if p.status != "infeasible":
                p.sensitivity = sensitivity(district, work, p.actions, seed, settings.sensitivity_samples)

    has_valid = any(p.status == "verified" for p in ordered)
    blocked_reason = None
    if not has_valid:
        unmet = {aid: r for aid, r in baseline.summary["requirements"].items() if not r["satisfied"]}
        blocked_reason = (
            "No candidate plan satisfies every hard constraint and scenario requirement. "
            f"Unmet requirements without action: {sorted(unmet)}. "
            "Review resources, routes and requirements; nothing has been approved."
        )
    return PlanningOutcome(
        baseline=baseline,
        plans=ordered,
        candidate_keys=keys,
        excluded_keys=excluded,
        requirement_clauses=clause_info,
        iterations=iterations,
        blocked=not has_valid,
        blocked_reason=blocked_reason,
        events=events,
    )


def params_with_budget(params: ScenarioParams, budget: float) -> ScenarioParams:
    if budget == params.resources.budget:
        return params
    p = params.model_copy(deep=True)
    p.resources.budget = budget
    return p


def _greedy(district: District, params: ScenarioParams, options: list[Option]) -> list[PlanAction]:
    """Benefit-per-cost greedy: take the best start per action, add while resources allow."""
    res = params.resources
    best: dict[str, Option] = {}
    for o in options:
        cur = best.get(o.key)
        if cur is None or o.benefit > cur.benefit or (o.benefit == cur.benefit and o.start < cur.start):
            best[o.key] = o
    ranked = sorted(
        (o for o in best.values() if o.benefit > 0),
        key=lambda o: (-(o.benefit / max(o.cost, 1.0)), o.start, o.key),
    )
    chosen: list[Option] = []
    crews = [0] * params.duration_h
    equip = dict.fromkeys(EQUIPMENT_KINDS, 0)
    spent = 0.0
    for o in ranked:
        if spent + o.cost > res.budget:
            continue
        if any(equip[k] + n > getattr(res, k) for k, n in o.equipment.items()):
            continue
        if any(crews[h] + o.crews > res.crews for h in range(o.start, o.end)):
            continue
        if any(
            (o.type, c.type) in MUTUALLY_EXCLUSIVE or (c.type, o.type) in MUTUALLY_EXCLUSIVE
            for c in chosen
            if c.target == o.target
        ):
            continue
        pre = PREREQUISITES.get(o.type)
        if pre and not any(c.type == pre and c.target == o.target and c.end <= o.start for c in chosen):
            continue
        chosen.append(o)
        spent += o.cost
        for k, n in o.equipment.items():
            equip[k] += n
        for h in range(o.start, o.end):
            crews[h] += o.crews
    return sorted((PlanAction(o.type, o.target, o.start) for o in chosen), key=lambda a: (a.start_h, a.type))
