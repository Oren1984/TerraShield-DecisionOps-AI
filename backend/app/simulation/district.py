"""Fictional district graph: zones, junctions, road edges and assets.

The district is static reference data loaded from ``scenarios/district.json``.
All physical values are demonstrative and uncalibrated.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import settings


@dataclass(frozen=True)
class Soil:
    porosity: float
    active_depth_mm: float
    initial_saturation: float
    drainage_rate_h: float


@dataclass(frozen=True)
class Zone:
    id: str
    name: str
    coastal: bool
    polygon: list[list[float]]
    area_km2: float
    base_elev_m: float
    imperviousness: float
    ponding_fraction: float
    gravity_drain_m3h: float
    outfall_elev_m: float | None
    crest_elev_m: float | None
    seawall_length_m: float
    downstream: list[tuple[str, float]]
    soil: Soil

    @property
    def area_m2(self) -> float:
        return self.area_km2 * 1_000_000

    @property
    def ponds(self) -> bool:
        return self.ponding_fraction > 0


@dataclass(frozen=True)
class Junction:
    id: str
    name: str
    x: float
    y: float
    zone: str


@dataclass(frozen=True)
class RoadEdge:
    id: str
    name: str
    a: str
    b: str
    zone: str
    length_km: float
    low_point_elev_m: float
    slope_susceptibility: float
    wind_exposure: float
    wind_closure_gust_ms: float | None


@dataclass(frozen=True)
class Asset:
    id: str
    name: str
    type: str
    junction: str
    x: float
    y: float
    zone: str
    elev_m: float
    criticality: int
    flood_fail_depth_m: float
    wind_fail_gust_ms: float
    wind_exposure: float
    ground_susceptibility: float
    powered_by: str | None
    pump_capacity_m3h: float = 0.0
    drains_zone: str | None = None


@dataclass(frozen=True)
class District:
    district_id: str
    version: str
    name: str
    depot_junction: str
    zones: dict[str, Zone]
    junctions: dict[str, Junction]
    edges: dict[str, RoadEdge]
    assets: dict[str, Asset]
    zone_access: dict[str, str]
    raw: dict[str, Any] = field(repr=False, compare=False)

    def zone_order(self) -> list[str]:
        """Zones ordered so that upstream zones are processed before downstream ones."""
        indeg = {z: 0 for z in self.zones}
        for z in self.zones.values():
            for target, _ in z.downstream:
                indeg[target] += 1
        queue = deque(sorted(z for z, d in indeg.items() if d == 0))
        order: list[str] = []
        while queue:
            zid = queue.popleft()
            order.append(zid)
            for target, _ in self.zones[zid].downstream:
                indeg[target] -= 1
                if indeg[target] == 0:
                    queue.append(target)
        if len(order) != len(self.zones):
            raise ValueError("zone routing graph contains a cycle")
        return order

    def reachable(self, open_edges: set[str]) -> set[str]:
        """Junctions reachable from the depot over the given open edges."""
        adj: dict[str, list[str]] = {j: [] for j in self.junctions}
        for eid in open_edges:
            e = self.edges[eid]
            adj[e.a].append(e.b)
            adj[e.b].append(e.a)
        seen = {self.depot_junction}
        queue = deque([self.depot_junction])
        while queue:
            cur = queue.popleft()
            for nxt in adj[cur]:
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        return seen

    def route(self, open_edges: set[str], target: str) -> list[str] | None:
        """Shortest (by length) edge path from depot to ``target`` over open edges."""
        import heapq

        dist = {self.depot_junction: 0.0}
        prev: dict[str, tuple[str, str]] = {}
        heap = [(0.0, self.depot_junction)]
        while heap:
            d, cur = heapq.heappop(heap)
            if cur == target:
                break
            if d > dist.get(cur, float("inf")):
                continue
            for eid in sorted(open_edges):
                e = self.edges[eid]
                if cur not in (e.a, e.b):
                    continue
                nxt = e.b if cur == e.a else e.a
                nd = d + e.length_km
                if nd < dist.get(nxt, float("inf")):
                    dist[nxt] = nd
                    prev[nxt] = (cur, eid)
                    heapq.heappush(heap, (nd, nxt))
        if target not in dist:
            return None
        path: list[str] = []
        cur = target
        while cur != self.depot_junction:
            p, eid = prev[cur]
            path.append(eid)
            cur = p
        return list(reversed(path))


def parse_district(data: dict[str, Any]) -> District:
    zones = {
        z["id"]: Zone(
            id=z["id"],
            name=z["name"],
            coastal=z["coastal"],
            polygon=z["polygon"],
            area_km2=z["area_km2"],
            base_elev_m=z["base_elev_m"],
            imperviousness=z["imperviousness"],
            ponding_fraction=z["ponding_fraction"],
            gravity_drain_m3h=z["gravity_drain_m3h"],
            outfall_elev_m=z["outfall_elev_m"],
            crest_elev_m=z["crest_elev_m"],
            seawall_length_m=z["seawall_length_m"],
            downstream=[(t, f) for t, f in z["downstream"]],
            soil=Soil(**z["soil"]),
        )
        for z in data["zones"]
    }
    junctions = {j["id"]: Junction(**j) for j in data["junctions"]}
    edges = {
        e["id"]: RoadEdge(
            id=e["id"],
            name=e["name"],
            a=e["from"],
            b=e["to"],
            zone=e["zone"],
            length_km=e["length_km"],
            low_point_elev_m=e["low_point_elev_m"],
            slope_susceptibility=e["slope_susceptibility"],
            wind_exposure=e["wind_exposure"],
            wind_closure_gust_ms=e["wind_closure_gust_ms"],
        )
        for e in data["road_edges"]
    }
    assets = {a["id"]: Asset(**a) for a in data["assets"]}
    for e in edges.values():
        if e.a not in junctions or e.b not in junctions or e.zone not in zones:
            raise ValueError(f"edge {e.id} references unknown junction or zone")
    for a in assets.values():
        if a.junction not in junctions or a.zone not in zones:
            raise ValueError(f"asset {a.id} references unknown junction or zone")
        if a.powered_by is not None and a.powered_by not in assets:
            raise ValueError(f"asset {a.id} powered by unknown asset")
    return District(
        district_id=data["district_id"],
        version=data["version"],
        name=data["name"],
        depot_junction=data["depot_junction"],
        zones=zones,
        junctions=junctions,
        edges=edges,
        assets=assets,
        zone_access=dict(data["zone_access"]),
        raw=data,
    )


@lru_cache(maxsize=1)
def load_district(path: str | None = None) -> District:
    p = Path(path) if path else Path(settings.scenario_dir) / "district.json"
    return parse_district(json.loads(p.read_text(encoding="utf-8")))
