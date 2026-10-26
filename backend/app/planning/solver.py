"""Bounded action selection and scheduling with OR-Tools CP-SAT.

The solver works on a linearised problem: each option is one action on one
target at one start hour, carrying a pre-computed benefit (simulated
disruption reduction). Interactions between actions are not linear, so every
solution is re-simulated and checked by the independent verifier afterwards.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ortools.sat.python import cp_model

SCALE = 1000  # objective scaling to integers


@dataclass(frozen=True)
class Option:
    key: str  # "type:target"
    type: str
    target: str
    start: int
    end: int
    crews: int
    equipment: dict[str, int]
    cost: float
    benefit: float


@dataclass
class Problem:
    options: list[Option]
    horizon: int
    crews: int
    equipment: dict[str, int]
    budget: float
    exclusive_groups: list[list[str]] = field(default_factory=list)  # keys; at most one key per group
    prerequisites: list[tuple[str, str]] = field(default_factory=list)  # (key, required key)
    coverage: list[list[int]] = field(default_factory=list)  # option indices; at least one chosen
    cost_weight: float = 1 / 2000  # benefit units per currency unit
    time_weight: float = 0.05  # benefit units per completion hour


@dataclass
class Solution:
    status: str  # optimal | time_limited | infeasible | unknown
    chosen: list[int]
    objective: float | None
    wall_s: float


def option_value(p: Problem, o: Option) -> int:
    return round((o.benefit - p.cost_weight * o.cost - p.time_weight * o.end) * SCALE)


def solve(p: Problem, time_limit_s: float = 10.0, workers: int = 4) -> Solution:
    m = cp_model.CpModel()
    x = [m.new_bool_var(f"x{i}") for i in range(len(p.options))]

    by_key: dict[str, list[int]] = {}
    for i, o in enumerate(p.options):
        by_key.setdefault(o.key, []).append(i)

    for idxs in by_key.values():
        m.add(sum(x[i] for i in idxs) <= 1)

    for h in range(p.horizon):
        active = [i for i, o in enumerate(p.options) if o.start <= h < o.end and o.crews]
        if active:
            m.add(sum(p.options[i].crews * x[i] for i in active) <= p.crews)

    for kind, available in p.equipment.items():
        users = [i for i, o in enumerate(p.options) if o.equipment.get(kind)]
        if users:
            m.add(sum(p.options[i].equipment[kind] * x[i] for i in users) <= available)

    m.add(sum(round(o.cost) * x[i] for i, o in enumerate(p.options)) <= round(p.budget))

    for group in p.exclusive_groups:
        idxs = [i for k in group for i in by_key.get(k, [])]
        if len(idxs) > 1:
            m.add(sum(x[i] for i in idxs) <= 1)

    for key, required in p.prerequisites:
        for i in by_key.get(key, []):
            ok = [j for j in by_key.get(required, []) if p.options[j].end <= p.options[i].start]
            if ok:
                m.add(x[i] <= sum(x[j] for j in ok))
            else:
                m.add(x[i] == 0)

    for clause in p.coverage:
        if not clause:
            # An empty clause cannot be satisfied.
            never = m.new_bool_var("empty_clause")
            m.add(never == 1)
            m.add(never == 0)
        else:
            m.add_bool_or([x[i] for i in clause])

    m.maximize(sum(option_value(p, o) * x[i] for i, o in enumerate(p.options)))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_workers = workers
    solver.parameters.random_seed = 0
    t0 = time.perf_counter()
    status = solver.solve(m)
    wall = time.perf_counter() - t0
    if status == cp_model.OPTIMAL:
        st = "optimal"
    elif status == cp_model.FEASIBLE:
        st = "time_limited"
    elif status == cp_model.INFEASIBLE:
        st = "infeasible"
    else:
        st = "unknown"
    if st in ("optimal", "time_limited"):
        chosen = [i for i in range(len(x)) if solver.value(x[i])]
        return Solution(st, chosen, solver.objective_value / SCALE, wall)
    return Solution(st, [], None, wall)
