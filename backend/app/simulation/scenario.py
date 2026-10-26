"""Scenario parameter contract.

Every numeric field carries an explicit unit and bounded range. Parameter
profiles are synthetic design curves, not observations.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _f(lo: float, hi: float, unit: str, desc: str, default: float) -> Any:
    return Field(default, ge=lo, le=hi, description=desc, json_schema_extra={"unit": unit})


class Rainfall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_mm_h: float = _f(0, 20, "mm/h", "Background rainfall intensity", 1.0)
    peak_mm_h: float = _f(0, 120, "mm/h", "Additional peak rainfall intensity", 25.0)
    peak_hour: float = _f(0, 48, "h", "Hour of peak rainfall", 6.0)
    spread_h: float = _f(0.5, 24, "h", "Standard deviation of the rainfall bump", 3.0)


class Coastal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mean_level_m: float = _f(0, 2.5, "m above datum", "Mean still-water level", 1.0)
    tide_amplitude_m: float = _f(0, 1.5, "m", "Semi-diurnal tide amplitude", 0.6)
    tide_phase_h: float = _f(0, 12.4, "h", "Hour of first high tide", 6.0)
    surge_peak_m: float = _f(0, 3.0, "m", "Peak storm surge added to the tide", 0.8)
    surge_peak_hour: float = _f(0, 48, "h", "Hour of peak surge", 6.0)
    surge_spread_h: float = _f(0.5, 24, "h", "Standard deviation of the surge bump", 3.0)


class Wind(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_ms: float = _f(0, 30, "m/s", "Background sustained wind speed", 6.0)
    peak_ms: float = _f(0, 50, "m/s", "Additional peak sustained wind speed", 12.0)
    peak_hour: float = _f(0, 48, "h", "Hour of peak wind", 6.0)
    spread_h: float = _f(0.5, 24, "h", "Standard deviation of the wind bump", 3.0)
    gust_factor: float = _f(1.0, 2.0, "ratio", "Gust to sustained wind ratio", 1.4)


class Resources(BaseModel):
    model_config = ConfigDict(extra="forbid")
    crews: int = Field(
        3, ge=0, le=12, description="Field crews available per hour", json_schema_extra={"unit": "crews"}
    )
    pumps: int = Field(1, ge=0, le=6, description="Temporary pump units", json_schema_extra={"unit": "units"})
    barriers: int = Field(2, ge=0, le=8, description="Demountable barrier sets", json_schema_extra={"unit": "sets"})
    generators: int = Field(1, ge=0, le=4, description="Mobile backup generators", json_schema_extra={"unit": "units"})
    comms_units: int = Field(
        0, ge=0, le=2, description="Mobile communications units", json_schema_extra={"unit": "units"}
    )
    budget: float = Field(
        40000, ge=0, le=500000, description="Response budget", json_schema_extra={"unit": "currency units"}
    )
    mobilization_h: int = Field(
        1, ge=0, le=6, description="Earliest hour any action can start", json_schema_extra={"unit": "h"}
    )


class ScenarioParams(BaseModel):
    """Inputs for one simulated event in the Harbor Vale district."""

    model_config = ConfigDict(extra="forbid")

    duration_h: int = Field(18, ge=6, le=48, description="Simulated duration", json_schema_extra={"unit": "h"})
    rainfall: Rainfall = Field(default_factory=Rainfall)
    coastal: Coastal = Field(default_factory=Coastal)
    wind: Wind = Field(default_factory=Wind)
    soil_saturation: dict[str, float] = Field(
        default_factory=dict,
        description="Initial soil saturation override per zone (0-1 fraction of pore space)",
        json_schema_extra={"unit": "fraction"},
    )
    resources: Resources = Field(default_factory=Resources)
    must_keep_operational: list[str] = Field(
        default_factory=list,
        description="Assets that a plan must keep operational (hard requirement)",
    )
    affected_assets: list[str] = Field(default_factory=list, description="Assets highlighted for this scenario")
    source_labels: dict[str, str] = Field(default_factory=dict, description="Provenance label per parameter group")
    notes: str = Field("", max_length=4000, description="Free-text notes; treated as untrusted data")

    @field_validator("soil_saturation")
    @classmethod
    def _sat_range(cls, v: dict[str, float]) -> dict[str, float]:
        for k, s in v.items():
            if not 0.0 <= s <= 1.0:
                raise ValueError(f"soil saturation for {k} must be between 0 and 1")
        return v

    def input_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()


def _bump(t: float, peak: float, center: float, spread: float) -> float:
    return peak * math.exp(-0.5 * ((t - center) / spread) ** 2)


def hazard_series(p: ScenarioParams) -> dict[str, list[float]]:
    """Hourly hazard drivers evaluated at hour midpoints."""
    rain, sea, wind, gust = [], [], [], []
    for h in range(p.duration_h):
        t = h + 0.5
        r = p.rainfall.base_mm_h + _bump(t, p.rainfall.peak_mm_h, p.rainfall.peak_hour, p.rainfall.spread_h)
        tide = p.coastal.tide_amplitude_m * math.cos(2 * math.pi * (t - p.coastal.tide_phase_h) / 12.42)
        s = (
            p.coastal.mean_level_m
            + tide
            + _bump(t, p.coastal.surge_peak_m, p.coastal.surge_peak_hour, p.coastal.surge_spread_h)
        )
        w = p.wind.base_ms + _bump(t, p.wind.peak_ms, p.wind.peak_hour, p.wind.spread_h)
        rain.append(round(r, 4))
        sea.append(round(s, 4))
        wind.append(round(w, 4))
        gust.append(round(w * p.wind.gust_factor, 4))
    return {"rain_mm_h": rain, "sea_level_m": sea, "wind_ms": wind, "gust_ms": gust}
