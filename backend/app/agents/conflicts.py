"""Deterministic conflict detection between validated specialist proposals.

The coordinator explains and resolves these conflicts; it cannot invent new
ones or dismiss hard restrictions.
"""

from __future__ import annotations

from typing import Any

from app.agents.evidence import access_starts, wind_starts
from app.agents.schemas import SpecialistAssessment
from app.simulation.actions import CATALOG, EQUIPMENT_KINDS, MUTUALLY_EXCLUSIVE
from app.simulation.district import District
from app.simulation.engine import SimResult
from app.simulation.scenario import ScenarioParams


def accepted_restrictions(assessments: dict[str, SpecialistAssessment]) -> dict[str, int]:
    g = assessments.get("ground")
    out: dict[str, int] = {}
    if g:
        for r in g.restrictions:
            out[r.edge_id] = min(out.get(r.edge_id, r.from_hour), r.from_hour)
    return out


def detect_conflicts(
    district: District,
    params: ScenarioParams,
    sim: SimResult,
    assessments: dict[str, SpecialistAssessment],
) -> list[dict[str, Any]]:
    restrictions = accepted_restrictions(assessments)
    conflicts: list[dict[str, Any]] = []

    def add(kind: str, roles: list[str], detail: str, **data: Any) -> None:
        conflicts.append({"id": f"C{len(conflicts) + 1}", "type": kind, "roles": roles, "detail": detail, **data})

    proposals = [(role, p) for role, a in assessments.items() for p in a.proposed_actions]
    for role, p in proposals:
        key = f"{p.action_type}:{p.target}"
        acc = access_starts(district, sim, p.action_type, p.target, restrictions, params)
        acc_no_restr = access_starts(district, sim, p.action_type, p.target, {}, params)
        wnd = wind_starts(district, sim, p.action_type, p.target, params)
        both = sorted(set(acc) & set(wnd))
        restricted_edges = sorted(restrictions)
        if not acc and acc_no_restr:
            add(
                "route_blocked_by_restriction",
                [role, "ground"],
                f"{key} needs a route that the ground restriction closes ({', '.join(restricted_edges)}); no start hour remains.",
                action=key,
                proposer=role,
                edges=restricted_edges,
                feasible_starts=[],
            )
        elif not acc:
            add(
                "route_unavailable",
                [role],
                f"{key} location is not reachable from the depot at any start hour.",
                action=key,
                proposer=role,
                feasible_starts=[],
            )
        elif len(acc) < len(acc_no_restr):
            add(
                "route_window_narrowed",
                [role, "ground"],
                f"Ground restrictions limit {key} to start hours {acc[0]}-{acc[-1]}.",
                action=key,
                proposer=role,
                edges=restricted_edges,
                feasible_starts=acc,
            )
        if not wnd:
            add(
                "wind_infeasible",
                [role, "air"],
                f"{key} exceeds its {CATALOG[p.action_type].wind_limit_ms:.0f} m/s working wind limit at every start hour.",
                action=key,
                proposer=role,
                feasible_starts=[],
            )
        elif acc and wnd and not both:
            add(
                "no_common_window",
                [role, "air", "ground"],
                f"{key} has access windows and wind windows, but they never overlap.",
                action=key,
                proposer=role,
                feasible_starts=[],
            )
        elif both and p.target in sim.summary["failed_assets"]:
            fail_h = sim.summary["failed_assets"][p.target]["hour"]
            in_time = [s for s in both if s + CATALOG[p.action_type].duration_h <= fail_h]
            if not in_time:
                access_in_time = any(s + CATALOG[p.action_type].duration_h <= fail_h for s in acc)
                limiting = "air" if access_in_time else role  # wind closed the window, otherwise access did
                add(
                    "too_late",
                    sorted({role, limiting}),
                    f"{key} can only start at hours {both[0]}-{both[-1]}, after {p.target} fails at hour {fail_h}.",
                    action=key,
                    proposer=role,
                    feasible_starts=both,
                    fail_hour=fail_h,
                )

    for kind in EQUIPMENT_KINDS:
        demand = sum(CATALOG[p.action_type].equipment.get(kind, 0) for _, p in proposals)
        available = getattr(params.resources, kind)
        if demand > available:
            add(
                "resource_shortfall",
                sorted({r for r, p in proposals if CATALOG[p.action_type].equipment.get(kind)}),
                f"Proposals need {demand} {kind} but only {available} are available.",
                resource=kind,
                demand=demand,
                available=available,
            )

    crew_hours = sum(CATALOG[p.action_type].crews * CATALOG[p.action_type].duration_h for _, p in proposals)
    capacity = params.resources.crews * (params.duration_h - params.resources.mobilization_h)
    if crew_hours > capacity:
        add(
            "resource_shortfall",
            sorted({r for r, _ in proposals}),
            f"Proposals need {crew_hours} crew-hours; {capacity} are available.",
            resource="crew_hours",
            demand=crew_hours,
            available=capacity,
        )
    cost = sum(CATALOG[p.action_type].cost for _, p in proposals)
    if cost > params.resources.budget:
        add(
            "resource_shortfall",
            sorted({r for r, _ in proposals}),
            f"Proposals cost {cost:.0f}; the budget is {params.resources.budget:.0f}.",
            resource="budget",
            demand=cost,
            available=params.resources.budget,
        )

    for t1, t2 in MUTUALLY_EXCLUSIVE:
        for tgt in sorted(
            {p.target for _, p in proposals if p.action_type == t1}
            & {p.target for _, p in proposals if p.action_type == t2}
        ):
            add(
                "mutually_exclusive",
                ["water"],
                f"{t1} and {t2} both target {tgt}; only one can be used.",
                action=f"{t1}:{tgt}",
                other=f"{t2}:{tgt}",
            )
    return conflicts
