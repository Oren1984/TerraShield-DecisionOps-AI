"""Structured evidence derived from the deterministic simulation.

Agents may only cite these IDs. Statements are generated from computed values;
agents interpret evidence but never supply measurements themselves.
"""

from __future__ import annotations

from typing import Any

from app.simulation.actions import CATALOG, action_location, site_wind_exposure, valid_targets
from app.simulation.district import District
from app.simulation.engine import GROUND_WARNING_SI, SimResult
from app.simulation.scenario import ScenarioParams

GROUND_RESTRICTION_SI = 0.45  # precautionary closure threshold applied by the ground role (policy)

ROLE_DOMAINS = {
    "water": {"water", "shared"},
    "air": {"air", "shared"},
    "ground": {"ground", "shared"},
    "coordinator": {"water", "air", "ground", "shared"},
}


def _item(eid: str, domain: str, kind: str, statement: str, **data: Any) -> dict[str, Any]:
    return {"id": eid, "domain": domain, "kind": kind, "statement": statement, "data": data}


def build_evidence(district: District, params: ScenarioParams, sim: SimResult) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    hz = sim.hazards
    s = sim.summary

    def peak(series: list[float]) -> tuple[float, int]:
        v = max(series)
        return v, series.index(v)

    r, rh = peak(hz["rain_mm_h"])
    items.append(
        _item(
            "ev-haz-rain-peak",
            "water",
            "hazard",
            f"Peak rainfall {r:.1f} mm/h at hour {rh}.",
            value=r,
            unit="mm/h",
            hour=rh,
        )
    )
    sl, sh = peak(hz["sea_level_m"])
    items.append(
        _item(
            "ev-haz-sea-peak",
            "water",
            "hazard",
            f"Peak coastal water level {sl:.2f} m above datum at hour {sh}.",
            value=sl,
            unit="m",
            hour=sh,
        )
    )
    w, wh = peak(hz["wind_ms"])
    items.append(
        _item(
            "ev-haz-wind-peak",
            "air",
            "hazard",
            f"Peak sustained wind {w:.1f} m/s (gust {w * params.wind.gust_factor:.1f} m/s) at hour {wh}.",
            value=w,
            unit="m/s",
            hour=wh,
        )
    )

    for zid, depth in s["peak_depth_m"].items():
        z = district.zones[zid]
        if z.ponds:
            hour = max(range(len(sim.hours)), key=lambda h: sim.hours[h]["zones"][zid]["depth_m"])
            items.append(
                _item(
                    f"ev-zone-{zid}-peak-depth",
                    "water",
                    "zone",
                    f"{z.name} ponding peaks at {depth:.2f} m depth (level {z.base_elev_m + depth:.2f} m) at hour {hour}.",
                    zone=zid,
                    value=depth,
                    unit="m",
                    hour=hour,
                )
            )
        sat_series = [h["zones"][zid]["saturation"] for h in sim.hours]
        smax = max(sat_series)
        items.append(
            _item(
                f"ev-zone-{zid}-saturation",
                "ground",
                "zone",
                f"{z.name} soil saturation reaches {smax:.2f} (fraction of pore space).",
                zone=zid,
                value=smax,
                unit="fraction",
            )
        )

    for aid, info in s["failed_assets"].items():
        a = district.assets[aid]
        domain = {"flood": "water", "wind": "air"}.get(info["cause"], "shared")
        items.append(
            _item(
                f"ev-asset-{aid}-failure",
                domain,
                "asset",
                f"Without action {a.name} fails at hour {info['hour']} ({info['cause']}).",
                asset=aid,
                hour=info["hour"],
                cause=info["cause"],
            )
        )

    for a in district.assets.values():
        depths = [h["assets"][a.id]["flood_depth_m"] for h in sim.hours]
        dmax = max(depths)
        if dmax > 0.05:
            items.append(
                _item(
                    f"ev-asset-{a.id}-flood-depth",
                    "water",
                    "asset",
                    f"{a.name} sees up to {dmax:.2f} m water above floor level (fails above {a.flood_fail_depth_m:.2f} m).",
                    asset=a.id,
                    value=dmax,
                    unit="m",
                    threshold=a.flood_fail_depth_m,
                )
            )
        gusts = [h["assets"][a.id]["site_gust_ms"] for h in sim.hours]
        gmax = max(gusts)
        if gmax >= 0.7 * a.wind_fail_gust_ms:
            items.append(
                _item(
                    f"ev-asset-{a.id}-gust",
                    "air",
                    "asset",
                    f"{a.name} site gust reaches {gmax:.1f} m/s against a failure threshold of {a.wind_fail_gust_ms:.0f} m/s.",
                    asset=a.id,
                    value=gmax,
                    unit="m/s",
                    threshold=a.wind_fail_gust_ms,
                )
            )
        unpowered = [h["hour"] for h in sim.hours if h["assets"][a.id]["state"] == "unpowered"]
        if unpowered:
            items.append(
                _item(
                    f"ev-asset-{a.id}-unpowered",
                    "water",
                    "dependency",
                    f"{a.name} loses power from hour {unpowered[0]} because {a.powered_by} fails.",
                    asset=a.id,
                    supplier=a.powered_by,
                    hour=unpowered[0],
                    hours=len(unpowered),
                )
            )
        unreachable = s["unreachable_hours"].get(a.id)
        if unreachable:
            first = next(h["hour"] for h in sim.hours if not h["assets"][a.id]["reachable"])
            items.append(
                _item(
                    f"ev-asset-{a.id}-access",
                    "shared",
                    "access",
                    f"{a.name} is unreachable from the depot for {unreachable} h, first at hour {first}.",
                    asset=a.id,
                    hours=unreachable,
                    first_hour=first,
                )
            )

    for eid, info in s["first_closure"].items():
        e = district.edges[eid]
        domain = {"ground": "ground", "wind": "air", "flood": "water", "restricted": "ground"}[info["cause"]]
        items.append(
            _item(
                f"ev-edge-{eid}-closure",
                domain,
                "road",
                f"{e.name} ({eid}) closes at hour {info['hour']} ({info['cause']}).",
                edge=eid,
                hour=info["hour"],
                cause=info["cause"],
            )
        )
    for eid, si in s["peak_edge_si"].items():
        if si > GROUND_WARNING_SI:
            e = district.edges[eid]
            first = next(h["hour"] for h in sim.hours if h["edge_si"][eid] > GROUND_WARNING_SI)
            over = next((h["hour"] for h in sim.hours if h["edge_si"][eid] > GROUND_RESTRICTION_SI), None)
            items.append(
                _item(
                    f"ev-edge-{eid}-ground-si",
                    "ground",
                    "road",
                    f"{e.name} ({eid}) slope susceptibility index peaks at {si:.2f}; above warning {GROUND_WARNING_SI} from hour {first}"
                    + (
                        f", above restriction level {GROUND_RESTRICTION_SI} from hour {over}."
                        if over is not None
                        else "."
                    ),
                    edge=eid,
                    value=si,
                    warning_from=first,
                    restriction_from=over,
                )
            )

    # Wind working windows for wind-limited actions on relevant targets.
    for t, spec in CATALOG.items():
        if spec.wind_limit_ms >= 25:
            continue
        for tgt in valid_targets(district, t):
            exp = site_wind_exposure(district, t, tgt)
            ok_starts = [
                st
                for st in range(params.resources.mobilization_h, params.duration_h - spec.duration_h + 1)
                if all(hz["wind_ms"][h] * exp <= spec.wind_limit_ms for h in range(st, st + spec.duration_h))
            ]
            if t in ("secure_mast", "brace_structure") or len(ok_starts) < params.duration_h - spec.duration_h:
                items.append(
                    _item(
                        f"ev-window-{t}-{tgt}",
                        "air",
                        "work_window",
                        f"{spec.label} at {tgt}: wind limit {spec.wind_limit_ms:.0f} m/s; "
                        + (
                            f"feasible start hours {ok_starts[0]}-{ok_starts[-1]}."
                            if ok_starts
                            else "no feasible start hour."
                        ),
                        action_type=t,
                        target=tgt,
                        feasible_starts=ok_starts,
                    )
                )

    for aid, r in s["requirements"].items():
        items.append(
            _item(
                f"ev-req-{aid}",
                "shared",
                "requirement",
                f"Requirement: keep {district.assets[aid].name} operational. Without action: "
                + ("satisfied." if r["satisfied"] else f"violated from hour {r['first_violation_hour']}."),
                asset=aid,
                **r,
            )
        )
    res = params.resources
    items.append(
        _item(
            "ev-resources",
            "shared",
            "resources",
            f"Available: {res.crews} crews, {res.pumps} pumps, {res.barriers} barrier sets, {res.generators} generators, "
            f"{res.comms_units} comms units, budget {res.budget:.0f}; earliest start hour {res.mobilization_h}.",
            **res.model_dump(),
        )
    )
    return items


def evidence_for_role(evidence: list[dict[str, Any]], role: str) -> list[dict[str, Any]]:
    domains = ROLE_DOMAINS[role]
    return [e for e in evidence if e["domain"] in domains]


def access_starts(
    district: District,
    sim: SimResult,
    action_type: str,
    target: str,
    restrictions: dict[str, int],
    params: ScenarioParams,
) -> list[int]:
    """Start hours where the action location is reachable, honouring restrictions."""
    spec = CATALOG[action_type]
    loc = action_location(district, action_type, target)
    out = []
    for st in range(params.resources.mobilization_h, params.duration_h - spec.duration_h + 1):
        open_edges = {
            e
            for e in district.edges
            if e not in sim.hours[st]["closed_edges"] and not (e in restrictions and st >= restrictions[e])
        }
        if loc in district.reachable(open_edges):
            out.append(st)
    return out


def wind_starts(district: District, sim: SimResult, action_type: str, target: str, params: ScenarioParams) -> list[int]:
    spec = CATALOG[action_type]
    exp = site_wind_exposure(district, action_type, target)
    return [
        st
        for st in range(params.resources.mobilization_h, params.duration_h - spec.duration_h + 1)
        if all(sim.hazards["wind_ms"][h] * exp <= spec.wind_limit_ms for h in range(st, st + spec.duration_h))
    ]
