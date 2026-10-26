"""Independent plan verifier.

Deliberately shares no code with the solver or the agent coordinator: it
re-reads the catalog and re-simulates the plan, then checks every hard
constraint against the plan's *own* simulated road closures. A plan is only
eligible for review when every check passes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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


@dataclass
class Check:
    constraint: str
    passed: bool
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"constraint": self.constraint, "passed": self.passed, "detail": self.detail}


@dataclass
class Verification:
    checks: list[Check]
    sim: SimResult | None

    @property
    def ok(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def hard_ok(self) -> bool:
        """All checks except the scenario requirement."""
        return all(c.passed for c in self.checks if c.constraint != "requirements")

    def violations(self) -> list[dict[str, Any]]:
        return [c.as_dict() for c in self.checks if not c.passed]


def verify_plan(
    district: District,
    params: ScenarioParams,
    plan: list[PlanAction],
    restrictions: dict[str, int] | None = None,
) -> Verification:
    checks: list[Check] = []
    res = params.resources

    # 1. Allowlist and targets
    bad = [
        {"type": a.type, "target": a.target, "start_h": a.start_h}
        for a in plan
        if a.type not in CATALOG or a.target not in valid_targets(district, a.type)
    ]
    checks.append(Check("allowlist", not bad, {"invalid_actions": bad}))
    if bad:
        # Nothing else can be evaluated safely for unknown actions.
        return Verification(checks, None)

    # 2. Each action/target at most once
    keys = [a.key() for a in plan]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    checks.append(Check("unique_actions", not dupes, {"duplicates": dupes}))

    # 3. Time window
    out_of_window = [a.as_dict() for a in plan if a.start_h < res.mobilization_h or a.end_h > params.duration_h]
    checks.append(
        Check(
            "time_window",
            not out_of_window,
            {
                "earliest_start_h": res.mobilization_h,
                "horizon_h": params.duration_h,
                "violations": out_of_window,
            },
        )
    )

    # 4. Budget
    cost = sum(CATALOG[a.type].cost for a in plan)
    checks.append(Check("budget", cost <= res.budget, {"cost": cost, "budget": res.budget}))

    # 5. Crews per hour (no double-booked teams)
    over: list[dict[str, int]] = []
    for h in range(params.duration_h):
        used = sum(CATALOG[a.type].crews for a in plan if a.start_h <= h < a.end_h)
        if used > res.crews:
            over.append({"hour": h, "used": used, "available": res.crews})
    checks.append(Check("crew_capacity", not over, {"over_capacity": over}))

    # 6. Equipment inventory
    eq_over = {}
    for kind in EQUIPMENT_KINDS:
        used = sum(CATALOG[a.type].equipment.get(kind, 0) for a in plan)
        available = getattr(res, kind)
        if used > available:
            eq_over[kind] = {"used": used, "available": available}
    checks.append(Check("equipment", not eq_over, {"over": eq_over}))

    # 7. Mutual exclusion
    conflicts = []
    for t1, t2 in MUTUALLY_EXCLUSIVE:
        targets1 = {a.target for a in plan if a.type == t1}
        targets2 = {a.target for a in plan if a.type == t2}
        for tgt in sorted(targets1 & targets2):
            conflicts.append({"types": [t1, t2], "target": tgt})
    checks.append(Check("mutual_exclusion", not conflicts, {"conflicts": conflicts}))

    # 8. Prerequisites
    missing = []
    for a in plan:
        pre = PREREQUISITES.get(a.type)
        if pre and not any(p.type == pre and p.target == a.target and p.end_h <= a.start_h for p in plan):
            missing.append({"action": a.as_dict(), "requires": pre})
    checks.append(Check("prerequisites", not missing, {"missing": missing}))

    sim = simulate(district, params, plan, restrictions)

    # 9. Wind working limits for the whole action window
    wind_bad = []
    for a in plan:
        limit = CATALOG[a.type].wind_limit_ms
        exp = site_wind_exposure(district, a.type, a.target)
        for h in range(a.start_h, min(a.end_h, params.duration_h)):
            site = sim.hazards["wind_ms"][h] * exp
            if site > limit:
                wind_bad.append({"action": a.as_dict(), "hour": h, "site_wind_ms": round(site, 2), "limit_ms": limit})
                break
    checks.append(Check("wind_limits", not wind_bad, {"violations": wind_bad}))

    # 10. Road access at start hour, using this plan's own simulated closures
    access_bad = []
    for a in plan:
        if a.start_h >= params.duration_h:
            continue
        loc = action_location(district, a.type, a.target)
        if loc not in sim.hours[a.start_h]["reachable_junctions"]:
            access_bad.append(
                {
                    "action": a.as_dict(),
                    "location": loc,
                    "closed_edges": sim.hours[a.start_h]["closed_edges"],
                }
            )
    checks.append(Check("road_access", not access_bad, {"violations": access_bad}))

    # 11. Scenario hard requirements (assets that must stay operational)
    req = sim.summary["requirements"]
    checks.append(Check("requirements", all(r["satisfied"] for r in req.values()), {"requirements": req}))
    return Verification(checks, sim)
