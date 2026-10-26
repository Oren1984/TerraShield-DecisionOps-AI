"""Planner, solver and independent verifier."""

from __future__ import annotations

import itertools

import pytest

from app.planning.planner import plan_scenario
from app.planning.solver import Option, Problem, option_value, solve
from app.planning.verifier import verify_plan
from app.simulation.engine import PlanAction


def _check(v, name):
    return next(c for c in v.checks if c.constraint == name)


# ------------------------------------------------------------------- verifier


def test_verifier_rejects_unknown_action_and_target(district, coastal):
    v = verify_plan(district, coastal, [PlanAction("approve_plan", "A1", 1)])
    assert not v.ok and _check(v, "allowlist").passed is False
    v = verify_plan(district, coastal, [PlanAction("secure_mast", "A1", 1)])
    assert not _check(v, "allowlist").passed


def test_verifier_detects_double_booked_crews(district, coastal):
    p = coastal.model_copy(deep=True)
    p.resources.crews = 1
    plan = [PlanAction("sandbag_asset", "A1", 1), PlanAction("sandbag_asset", "A2", 2)]
    v = verify_plan(district, p, plan)
    over = _check(v, "crew_capacity")
    assert not over.passed and over.detail["over_capacity"][0]["hour"] == 2


def test_verifier_budget_equipment_exclusion_prereq_window(district, coastal):
    p = coastal.model_copy(deep=True)
    p.resources.budget = 5000
    p.resources.barriers = 0
    plan = [
        PlanAction("flood_barrier_asset", "A1", 1),
        PlanAction("sandbag_asset", "A1", 1),
        PlanAction("temporary_pump", "Z1", 1),
        PlanAction("clear_drains", "Z2", 17),
    ]
    v = verify_plan(district, p, plan)
    failed = {c.constraint for c in v.checks if not c.passed}
    assert {"budget", "equipment", "mutual_exclusion", "prerequisites", "time_window"} <= failed


def test_verifier_blocks_route_closed_in_plan_simulation(district, conflict):
    # E2/E10 are ground-closed from hour 0 and Quay roads flood by hour 5: J3 is unreachable late.
    v = verify_plan(district, conflict, [PlanAction("sandbag_asset", "A2", 9)])
    assert not _check(v, "road_access").passed


def test_verifier_wind_limit(district, windy):
    v = verify_plan(district, windy, [PlanAction("secure_mast", "A8", 3)])
    assert not _check(v, "wind_limits").passed


def test_verifier_requirement(district, windy):
    assert not _check(verify_plan(district, windy, []), "requirements").passed
    assert verify_plan(district, windy, [PlanAction("mobile_comms_unit", "A8", 1)]).ok


# ------------------------------------------------------------------- solver vs exhaustive enumeration


def _feasible(p: Problem, chosen: tuple[int, ...]) -> bool:
    opts = [p.options[i] for i in chosen]
    keys = [o.key for o in opts]
    if len(keys) != len(set(keys)):
        return False
    if sum(o.cost for o in opts) > p.budget:
        return False
    for kind, avail in p.equipment.items():
        if sum(o.equipment.get(kind, 0) for o in opts) > avail:
            return False
    for h in range(p.horizon):
        if sum(o.crews for o in opts if o.start <= h < o.end) > p.crews:
            return False
    for group in p.exclusive_groups:
        if sum(1 for o in opts if o.key in group) > 1:
            return False
    for key, req in p.prerequisites:
        for o in opts:
            if o.key == key and not any(q.key == req and q.end <= o.start for q in opts):
                return False
    return all(any(i in chosen for i in clause) for clause in p.coverage)


def _brute(p: Problem) -> float | None:
    best = None
    n = len(p.options)
    for r in range(n + 1):
        for chosen in itertools.combinations(range(n), r):
            if _feasible(p, chosen):
                val = sum(option_value(p, p.options[i]) for i in chosen)
                best = val if best is None else max(best, val)
    return best


def _opt(key, start, dur, crews=1, cost=1000.0, benefit=5.0, equipment=None):
    t, tgt = key.split(":")
    return Option(key, t, tgt, start, start + dur, crews, equipment or {}, cost, benefit)


TINY = [
    Problem(
        [
            _opt("a:X", 0, 2, benefit=4),
            _opt("a:X", 1, 2, benefit=6),
            _opt("b:Y", 0, 3, benefit=5),
            _opt("c:Z", 1, 2, benefit=3, equipment={"pumps": 1}),
        ],
        horizon=4,
        crews=1,
        equipment={"pumps": 1},
        budget=5000,
    ),
    Problem(
        [
            _opt("a:X", 0, 2, benefit=4, cost=3000),
            _opt("b:Y", 0, 2, benefit=4, cost=3000),
            _opt("c:Z", 2, 2, benefit=2, cost=500),
        ],
        horizon=4,
        crews=2,
        equipment={},
        budget=5000,
        exclusive_groups=[["a:X", "b:Y"]],
    ),
    Problem(
        [
            _opt("pre:Z", 0, 2, benefit=0),
            _opt("pump:Z", 2, 2, benefit=9),
            _opt("pump:Z", 1, 2, benefit=9),
            _opt("q:W", 0, 4, benefit=3),
        ],
        horizon=5,
        crews=1,
        equipment={},
        budget=10000,
        prerequisites=[("pump:Z", "pre:Z")],
    ),
    Problem(
        [_opt("a:X", 0, 1, benefit=1), _opt("b:Y", 0, 1, benefit=-1), _opt("c:Z", 0, 1, benefit=3)],
        horizon=2,
        crews=1,
        equipment={},
        budget=10000,
        coverage=[[1]],
    ),
]


@pytest.mark.parametrize("prob", TINY)
def test_cpsat_matches_exhaustive_enumeration(prob):
    sol = solve(prob, time_limit_s=5)
    best = _brute(prob)
    assert sol.status == "optimal"
    assert _feasible(prob, tuple(sol.chosen))
    assert sum(option_value(prob, prob.options[i]) for i in sol.chosen) == best


def test_cpsat_reports_infeasible():
    prob = Problem([_opt("a:X", 0, 1, cost=9000)], horizon=2, crews=1, equipment={}, budget=100, coverage=[[0]])
    assert solve(prob, time_limit_s=2).status == "infeasible"
    prob2 = Problem([_opt("a:X", 0, 1)], horizon=2, crews=1, equipment={}, budget=100, coverage=[[]])
    assert solve(prob2, time_limit_s=2).status == "infeasible"


# ------------------------------------------------------------------- planner end to end


def test_planner_returns_only_verified_ranked_plans(district, coastal):
    out = plan_scenario(district, coastal, seed=1, with_sensitivity=False)
    ranked = out.ranked()
    assert ranked and all(p.verification.ok for p in ranked)
    scores = [p.objective()["score"] for p in ranked]
    assert scores == sorted(scores)
    labels = {p.label for p in out.plans}
    assert {"baseline", "cpsat_balanced"} <= labels
    best = ranked[0]
    assert best.objective()["disruption_total"] < out.baseline.summary["disruption_total"]


def test_planner_blocks_when_requirement_unmeetable(district, conflict):
    out = plan_scenario(district, conflict, seed=1, with_sensitivity=False)
    assert out.blocked and "A2" in (out.blocked_reason or "")
    assert not any(p.status == "verified" for p in out.plans)


def test_planner_finds_plan_with_more_resources(district, conflict):
    p = conflict.model_copy(deep=True)
    p.resources.crews, p.resources.barriers, p.resources.budget = 3, 2, 40000
    out = plan_scenario(district, p, seed=1, with_sensitivity=False)
    assert not out.blocked
    assert out.ranked()[0].verification.ok


def test_planner_substitutes_wind_infeasible_action(district, windy):
    out = plan_scenario(district, windy, seed=1, with_sensitivity=False)
    best = out.ranked()[0]
    assert any(a.type == "mobile_comms_unit" for a in best.actions)
    assert not any(a.type == "secure_mast" for a in best.actions)


def test_planner_honours_exclusions_budget_cap_and_restrictions(district, coastal):
    out = plan_scenario(
        district,
        coastal,
        seed=1,
        excluded_keys=["sandbag_asset:A2"],
        budget_cap=8000,
        restrictions={"E5": 1},
        with_sensitivity=False,
    )
    for p in out.plans:
        assert all(a.key() != "sandbag_asset:A2" for a in p.actions)
        if p.status == "verified":
            assert p.cost <= 8000
            # E5 restricted from hour 1: J3 (Quay South) is still reachable via the causeway, J2 via harbour roads.
            assert p.verification.ok
