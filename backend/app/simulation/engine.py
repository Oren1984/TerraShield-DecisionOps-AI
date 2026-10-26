"""Deterministic hourly simulator.

Model summary (all simplified and uncalibrated, see docs/ARCHITECTURE.md):

* Soil: bucket model per zone, S <- clamp(S + I/(n*D) - k*S), I = infiltrated rain (mm).
* Runoff: coefficient c = imperviousness + (1 - imperviousness) * S^2.
* Upland zones pass runoff exceeding gravity drainage capacity downstream.
* Coastal/ponding zones keep a storage balance V <- V + in - out with
  in = runoff + upstream excess + overtopping, out = min(V + in, gravity + pumps).
  Overtopping uses a lumped weir form q = C * (sea - crest)^1.5 per metre of wall.
* Hazard intensity (water level, gust, susceptibility index) is separated from
  exposure (asset elevation / exposure factor) and vulnerability (fragility thresholds).
* Pump and power state use the previous hour's asset state (explicit one-step lag).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.simulation.actions import CATALOG
from app.simulation.district import District
from app.simulation.scenario import ScenarioParams, hazard_series

OVERTOP_COEFF = 0.05  # m^0.5/s, lumped and demonstrative
TEMP_PUMP_M3H = 4000.0
OUTFALL_BLOCK_RANGE_M = 0.5
ROAD_FLOOD_CLOSURE_M = 0.25
GROUND_CLOSURE_SI = 0.5
GROUND_WARNING_SI = 0.35
ASSET_GROUND_DEGRADE_SI = 0.3
FLOOD_DEGRADE_M = 0.05
WIND_DEGRADE_RATIO = 0.85
ACCESS_PENALTY = 0.3

SEVERITY = {"operational": 0.0, "degraded": 0.4, "unpowered": 0.8, "failed": 1.0}


@dataclass(frozen=True)
class PlanAction:
    type: str
    target: str
    start_h: int

    @property
    def end_h(self) -> int:
        return self.start_h + CATALOG[self.type].duration_h

    def key(self) -> str:
        return f"{self.type}:{self.target}"

    def as_dict(self) -> dict[str, Any]:
        return {"type": self.type, "target": self.target, "start_h": self.start_h, "end_h": self.end_h}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> PlanAction:
        return PlanAction(type=d["type"], target=d["target"], start_h=int(d["start_h"]))


@dataclass
class _Effects:
    protection_m: dict[str, float] = field(default_factory=dict)
    crest_raise_m: dict[str, float] = field(default_factory=dict)
    temp_pumps: dict[str, int] = field(default_factory=dict)
    drain_factor: dict[str, float] = field(default_factory=dict)
    generator: set[str] = field(default_factory=set)
    wind_bonus: dict[str, float] = field(default_factory=dict)
    comms_substitute: set[str] = field(default_factory=set)
    slope_factor: dict[str, float] = field(default_factory=dict)


def _effects_at(plan: list[PlanAction], hour: int) -> _Effects:
    e = _Effects()
    for a in plan:
        if a.end_h > hour:
            continue
        if a.type == "sandbag_asset":
            e.protection_m[a.target] = max(e.protection_m.get(a.target, 0.0), 0.4)
        elif a.type == "flood_barrier_asset":
            e.protection_m[a.target] = max(e.protection_m.get(a.target, 0.0), 1.0)
        elif a.type == "temporary_flood_wall":
            e.crest_raise_m[a.target] = 0.6
        elif a.type == "temporary_pump":
            e.temp_pumps[a.target] = e.temp_pumps.get(a.target, 0) + 1
        elif a.type == "clear_drains":
            e.drain_factor[a.target] = 1.3
        elif a.type == "backup_generator":
            e.generator.add(a.target)
        elif a.type == "brace_structure":
            e.wind_bonus[a.target] = e.wind_bonus.get(a.target, 0.0) + 6.0
        elif a.type == "secure_mast":
            e.wind_bonus[a.target] = e.wind_bonus.get(a.target, 0.0) + 8.0
        elif a.type == "mobile_comms_unit":
            e.comms_substitute.add(a.target)
        elif a.type == "slope_drainage":
            e.slope_factor[a.target] = 0.6
    return e


@dataclass
class SimResult:
    hazards: dict[str, list[float]]
    hours: list[dict[str, Any]]
    summary: dict[str, Any]
    balance: dict[str, float]

    def closures_by_hour(self) -> list[set[str]]:
        return [set(h["closed_edges"]) for h in self.hours]

    def open_edges_at(self, hour: int, district: District) -> set[str]:
        return set(district.edges) - set(self.hours[hour]["closed_edges"])

    def to_dict(self) -> dict[str, Any]:
        return {"hazards": self.hazards, "hours": self.hours, "summary": self.summary, "balance": self.balance}


def simulate(
    district: District,
    params: ScenarioParams,
    plan: list[PlanAction] | None = None,
    restrictions: dict[str, int] | None = None,
) -> SimResult:
    """Run the hourly model. ``restrictions`` maps edge id -> hour from which an accepted
    specialist restriction closes the edge (administrative closure)."""
    plan = plan or []
    restrictions = restrictions or {}
    hz = hazard_series(params)
    zones = district.zones
    order = district.zone_order()

    sat = {zid: params.soil_saturation.get(zid, z.soil.initial_saturation) for zid, z in zones.items()}
    storage = {zid: 0.0 for zid in zones}
    damage: dict[str, str] = {aid: "operational" for aid in district.assets}
    failed_at: dict[str, int] = {}
    fail_cause: dict[str, str] = {}
    functional_prev = {aid: True for aid in district.assets}
    ground_closed: dict[str, int] = {}

    total_in = total_out = 0.0
    hours: list[dict[str, Any]] = []
    disruption_total = 0.0
    peak_depth = {zid: 0.0 for zid in zones}
    peak_si: dict[str, float] = {eid: 0.0 for eid in district.edges}
    first_closed: dict[str, dict[str, Any]] = {}
    unreachable_hours: dict[str, int] = {aid: 0 for aid in district.assets}
    nonfunctional_hours: dict[str, int] = {aid: 0 for aid in district.assets}

    for h in range(params.duration_h):
        eff = _effects_at(plan, h)
        rain = hz["rain_mm_h"][h]
        sea = hz["sea_level_m"][h]
        gust = hz["gust_ms"][h]

        zone_out: dict[str, dict[str, float]] = {}
        routed_in = {zid: 0.0 for zid in zones}
        for zid in order:
            z = zones[zid]
            s = sat[zid]
            c = z.imperviousness + (1 - z.imperviousness) * s * s
            runoff = rain / 1000.0 * c * z.area_m2
            infil_mm = rain * (1 - c)
            sat[zid] = min(
                1.0, max(0.0, s + infil_mm / (z.soil.porosity * z.soil.active_depth_mm) - z.soil.drainage_rate_h * s)
            )
            total_in += runoff
            gravity = z.gravity_drain_m3h * eff.drain_factor.get(zid, 1.0)
            overtop = 0.0
            if z.coastal and z.outfall_elev_m is not None:
                block = min(1.0, max(0.0, (sea - z.outfall_elev_m) / OUTFALL_BLOCK_RANGE_M))
                gravity *= 1 - block
                crest = (z.crest_elev_m or 0.0) + eff.crest_raise_m.get(zid, 0.0)
                if sea > crest:
                    overtop = OVERTOP_COEFF * (sea - crest) ** 1.5 * z.seawall_length_m * 3600
                    total_in += overtop
            inflow = runoff + routed_in[zid] + overtop
            if not z.ponds:
                drained = min(inflow, gravity)
                excess = inflow - drained
                total_out += drained
                for target, frac in z.downstream:
                    routed_in[target] += excess * frac
                zone_out[zid] = {
                    "inflow_m3": inflow,
                    "outflow_m3": drained,
                    "overtop_m3": overtop,
                    "storage_m3": 0.0,
                    "depth_m": 0.0,
                    "level_m": z.base_elev_m,
                }
                continue
            pumps = sum(
                a.pump_capacity_m3h for a in district.assets.values() if a.drains_zone == zid and functional_prev[a.id]
            )
            pumps += TEMP_PUMP_M3H * eff.temp_pumps.get(zid, 0)
            available = storage[zid] + inflow
            out = min(available, gravity + pumps)
            storage[zid] = available - out
            total_out += out
            depth = storage[zid] / (z.area_m2 * z.ponding_fraction)
            peak_depth[zid] = max(peak_depth[zid], depth)
            zone_out[zid] = {
                "inflow_m3": inflow,
                "outflow_m3": out,
                "overtop_m3": overtop,
                "storage_m3": storage[zid],
                "depth_m": depth,
                "level_m": z.base_elev_m + depth,
                "pump_capacity_m3h": pumps,
                "gravity_capacity_m3h": gravity,
            }

        # Road status (hazard intensity -> closure rules)
        closed: dict[str, str] = {}
        edge_si: dict[str, float] = {}
        for e in district.edges.values():
            s = sat[e.zone]
            si = e.slope_susceptibility * s * s * eff.slope_factor.get(e.id, 1.0)
            edge_si[e.id] = round(si, 4)
            peak_si[e.id] = max(peak_si[e.id], si)
            if e.id in restrictions and h >= restrictions[e.id]:
                closed[e.id] = "restricted"
            elif e.id in ground_closed or si > GROUND_CLOSURE_SI:
                ground_closed.setdefault(e.id, h)
                closed[e.id] = "ground"
            elif zones[e.zone].ponds and zone_out[e.zone]["level_m"] - e.low_point_elev_m > ROAD_FLOOD_CLOSURE_M:
                closed[e.id] = "flood"
            elif e.wind_closure_gust_ms is not None and gust * e.wind_exposure > e.wind_closure_gust_ms:
                closed[e.id] = "wind"
            if e.id in closed and e.id not in first_closed:
                first_closed[e.id] = {"hour": h, "cause": closed[e.id]}
        open_edges = set(district.edges) - set(closed)
        reachable = district.reachable(open_edges)

        # Asset state (exposure x vulnerability)
        asset_rows: dict[str, dict[str, Any]] = {}
        for a in district.assets.values():
            z = zones[a.zone]
            level = zone_out[a.zone]["level_m"] if z.ponds else z.base_elev_m
            depth = max(0.0, level - a.elev_m - eff.protection_m.get(a.id, 0.0))
            site_gust = gust * a.wind_exposure
            thr = a.wind_fail_gust_ms + eff.wind_bonus.get(a.id, 0.0)
            si = a.ground_susceptibility * sat[a.zone] ** 2
            causes: list[str] = []
            if damage[a.id] != "failed":
                if depth > a.flood_fail_depth_m:
                    damage[a.id] = "failed"
                    fail_cause[a.id] = "flood"
                elif site_gust >= thr:
                    damage[a.id] = "failed"
                    fail_cause[a.id] = "wind"
                if damage[a.id] == "failed":
                    failed_at[a.id] = h
            if damage[a.id] != "failed":
                if depth > FLOOD_DEGRADE_M:
                    causes.append("flood")
                if site_gust >= WIND_DEGRADE_RATIO * thr:
                    causes.append("wind")
                if si > ASSET_GROUND_DEGRADE_SI:
                    causes.append("ground")
            asset_rows[a.id] = {
                "flood_depth_m": round(depth, 4),
                "site_gust_ms": round(site_gust, 3),
                "wind_threshold_ms": thr,
                "ground_si": round(si, 4),
                "damage": damage[a.id] if damage[a.id] == "failed" else ("degraded" if causes else "operational"),
                "causes": [fail_cause[a.id]] if damage[a.id] == "failed" else causes,
            }

        # Dependencies: grid-powered assets lose function when their supply is not functional.
        functional: dict[str, bool] = {}
        for a in district.assets.values():
            ok = asset_rows[a.id]["damage"] != "failed"
            if ok and a.powered_by and a.id not in eff.generator:
                ok = asset_rows[a.powered_by]["damage"] != "failed"
            functional[a.id] = ok
        disruption_h = 0.0
        for a in district.assets.values():
            row = asset_rows[a.id]
            state = row["damage"]
            if state != "failed" and not functional[a.id]:
                state = "unpowered"
            if state == "failed" and a.id in eff.comms_substitute:
                state = "degraded"
                row["substituted"] = True
            row["state"] = state
            row["reachable"] = a.junction in reachable
            sev = SEVERITY[state] * a.criticality
            if not row["reachable"]:
                sev += ACCESS_PENALTY * a.criticality
                unreachable_hours[a.id] += 1
            if state in ("failed", "unpowered"):
                nonfunctional_hours[a.id] += 1
            disruption_h += sev
        disruption_total += disruption_h
        functional_prev = functional

        hours.append(
            {
                "hour": h,
                "rain_mm_h": rain,
                "sea_level_m": sea,
                "wind_ms": hz["wind_ms"][h],
                "gust_ms": gust,
                "zones": {
                    zid: {k: round(v, 4) for k, v in zo.items()} | {"saturation": round(sat[zid], 4)}
                    for zid, zo in zone_out.items()
                },
                "edge_si": edge_si,
                "closed_edges": closed,
                "reachable_junctions": sorted(reachable),
                "assets": asset_rows,
                "disruption": round(disruption_h, 4),
                "active_actions": [a.key() for a in plan if a.start_h <= h < a.end_h],
                "completed_actions": [a.key() for a in plan if a.end_h <= h],
            }
        )

    final_storage = sum(storage.values())
    final_state = hours[-1]["assets"] if hours else {}
    summary = {
        "disruption_total": round(disruption_total, 3),
        "disruption_peak": round(max((x["disruption"] for x in hours), default=0.0), 3),
        "failed_assets": {aid: {"hour": failed_at[aid], "cause": fail_cause[aid]} for aid in sorted(failed_at)},
        "nonfunctional_hours": {k: v for k, v in nonfunctional_hours.items() if v},
        "unreachable_hours": {k: v for k, v in unreachable_hours.items() if v},
        "peak_depth_m": {k: round(v, 4) for k, v in peak_depth.items()},
        "peak_edge_si": {k: round(v, 4) for k, v in peak_si.items()},
        "first_closure": first_closed,
        "final_states": {aid: r["state"] for aid, r in final_state.items()},
        "requirements": _requirements(params, hours),
    }
    balance = {
        "total_inflow_m3": round(total_in, 3),
        "total_outflow_m3": round(total_out, 3),
        "final_storage_m3": round(final_storage, 3),
        "residual_m3": round(total_in - total_out - final_storage, 6),
    }
    return SimResult(hazards=hz, hours=hours, summary=summary, balance=balance)


def _requirements(params: ScenarioParams, hours: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for aid in params.must_keep_operational:
        bad = [x["hour"] for x in hours if x["assets"][aid]["state"] in ("failed", "unpowered")]
        out[aid] = {"satisfied": not bad, "first_violation_hour": bad[0] if bad else None}
    return out


def requirements_met(result: SimResult) -> bool:
    return all(r["satisfied"] for r in result.summary["requirements"].values())
