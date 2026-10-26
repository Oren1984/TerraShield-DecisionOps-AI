"""Allowlisted response actions and their simulated effects.

This catalog is the only set of actions any planner or agent may propose.
Effects are simplified, demonstrative modifiers of the simulator state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.simulation.district import District

TargetKind = Literal["asset", "zone", "edge"]


@dataclass(frozen=True)
class ActionSpec:
    type: str
    label: str
    target_kind: TargetKind
    crews: int
    equipment: dict[str, int]
    duration_h: int
    cost: float
    wind_limit_ms: float
    description: str
    specialist: str  # role that normally proposes it


CATALOG: dict[str, ActionSpec] = {
    s.type: s
    for s in [
        ActionSpec(
            "sandbag_asset",
            "Sandbag perimeter",
            "asset",
            1,
            {},
            2,
            3000,
            25,
            "Raises local flood protection of an asset by 0.4 m.",
            "water",
        ),
        ActionSpec(
            "flood_barrier_asset",
            "Demountable flood barrier",
            "asset",
            1,
            {"barriers": 1},
            3,
            12000,
            18,
            "Raises local flood protection of an asset by 1.0 m.",
            "water",
        ),
        ActionSpec(
            "temporary_flood_wall",
            "Temporary flood wall",
            "zone",
            2,
            {"barriers": 2},
            4,
            20000,
            18,
            "Raises the effective seawall crest of a coastal zone by 0.6 m.",
            "water",
        ),
        ActionSpec(
            "temporary_pump",
            "Temporary pump",
            "zone",
            1,
            {"pumps": 1},
            2,
            5000,
            25,
            "Adds 4000 m3/h self-powered pumping in a ponding zone. Requires cleared drains.",
            "water",
        ),
        ActionSpec(
            "clear_drains",
            "Clear drains and outfalls",
            "zone",
            1,
            {},
            3,
            2500,
            25,
            "Increases gravity drainage capacity of a zone by 30%.",
            "water",
        ),
        ActionSpec(
            "backup_generator",
            "Connect backup generator",
            "asset",
            1,
            {"generators": 1},
            2,
            7000,
            25,
            "Keeps a grid-dependent asset powered if its supply fails.",
            "water",
        ),
        ActionSpec(
            "brace_structure",
            "Brace roof and cladding",
            "asset",
            1,
            {},
            3,
            4000,
            15,
            "Elevated work; raises an asset's wind failure threshold by 6 m/s.",
            "air",
        ),
        ActionSpec(
            "secure_mast",
            "Secure telecom mast",
            "asset",
            1,
            {},
            3,
            6000,
            12,
            "Elevated work on the mast; raises its wind failure threshold by 8 m/s.",
            "air",
        ),
        ActionSpec(
            "mobile_comms_unit",
            "Mobile communications unit",
            "asset",
            1,
            {"comms_units": 1},
            2,
            4000,
            25,
            "Deployed from the depot; substitutes telecom function if the mast fails.",
            "air",
        ),
        ActionSpec(
            "slope_drainage",
            "Install slope drainage",
            "edge",
            2,
            {},
            4,
            9000,
            25,
            "Reduces the ground-susceptibility index of a road slope by 40%.",
            "ground",
        ),
    ]
}

MUTUALLY_EXCLUSIVE: list[tuple[str, str]] = [("sandbag_asset", "flood_barrier_asset")]
"""Pairs of action types that cannot both target the same object."""

PREREQUISITES: dict[str, str] = {"temporary_pump": "clear_drains"}
"""Action type -> action type that must be completed on the same target before it starts."""

EQUIPMENT_KINDS = ("pumps", "barriers", "generators", "comms_units")


def valid_targets(district: District, action_type: str) -> list[str]:
    """Targets for which an action type is meaningful."""
    spec = CATALOG[action_type]
    if action_type in ("sandbag_asset", "flood_barrier_asset"):
        return [a.id for a in district.assets.values() if district.zones[a.zone].ponds]
    if action_type == "temporary_flood_wall":
        return [z.id for z in district.zones.values() if z.coastal]
    if action_type == "temporary_pump":
        return [z.id for z in district.zones.values() if z.ponds]
    if action_type == "clear_drains":
        return list(district.zones)
    if action_type == "backup_generator":
        return [a.id for a in district.assets.values() if a.powered_by]
    if action_type == "brace_structure":
        return [a.id for a in district.assets.values() if a.type in ("warehouse", "shelter", "clinic")]
    if action_type in ("secure_mast", "mobile_comms_unit"):
        return [a.id for a in district.assets.values() if a.type == "telecom"]
    if action_type == "slope_drainage":
        return [e.id for e in district.edges.values() if e.slope_susceptibility >= 0.5]
    raise KeyError(f"unknown target rule for {spec.type}")


def action_location(district: District, action_type: str, target: str) -> str:
    """Junction a crew must reach to perform the action."""
    if action_type == "mobile_comms_unit":
        return district.depot_junction  # deployed from the depot
    kind = CATALOG[action_type].target_kind
    if kind == "asset":
        return district.assets[target].junction
    if kind == "zone":
        return district.zone_access[target]
    return district.edges[target].a


def site_wind_exposure(district: District, action_type: str, target: str) -> float:
    if action_type == "mobile_comms_unit":
        return 1.0
    kind = CATALOG[action_type].target_kind
    if kind == "asset":
        return district.assets[target].wind_exposure
    if kind == "edge":
        return district.edges[target].wind_exposure
    return 1.0


def catalog_payload(district: District) -> list[dict]:
    return [
        {
            "type": s.type,
            "label": s.label,
            "target_kind": s.target_kind,
            "crews": s.crews,
            "equipment": s.equipment,
            "duration_h": s.duration_h,
            "cost": s.cost,
            "wind_limit_ms": s.wind_limit_ms,
            "description": s.description,
            "specialist": s.specialist,
            "valid_targets": valid_targets(district, s.type),
            "prerequisite": PREREQUISITES.get(s.type),
        }
        for s in CATALOG.values()
    ]
