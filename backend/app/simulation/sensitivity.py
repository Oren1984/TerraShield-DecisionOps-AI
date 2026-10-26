"""Seeded parameter perturbations.

The resulting ranges describe how sensitive the simulated outcome is to the
chosen inputs. They are NOT calibrated event probabilities or confidence intervals.
"""

from __future__ import annotations

import random
from typing import Any

from app.simulation.district import District
from app.simulation.engine import PlanAction, simulate
from app.simulation.scenario import ScenarioParams

PERTURBATION = {
    "rainfall_peak_rel_sd": 0.12,
    "surge_peak_abs_sd_m": 0.15,
    "wind_peak_rel_sd": 0.08,
    "saturation_abs_sd": 0.04,
}


def perturbed(params: ScenarioParams, rng: random.Random, district: District) -> ScenarioParams:
    p = params.model_copy(deep=True)
    p.rainfall.peak_mm_h = max(0.0, p.rainfall.peak_mm_h * rng.gauss(1, PERTURBATION["rainfall_peak_rel_sd"]))
    p.coastal.surge_peak_m = min(
        3.0, max(0.0, p.coastal.surge_peak_m + rng.gauss(0, PERTURBATION["surge_peak_abs_sd_m"]))
    )
    p.wind.peak_ms = min(50.0, max(0.0, p.wind.peak_ms * rng.gauss(1, PERTURBATION["wind_peak_rel_sd"])))
    sat = {}
    for zid, z in district.zones.items():
        base = params.soil_saturation.get(zid, z.soil.initial_saturation)
        sat[zid] = min(1.0, max(0.0, base + rng.gauss(0, PERTURBATION["saturation_abs_sd"])))
    p.soil_saturation = sat
    return p


def _pct(values: list[float], q: float) -> float:
    s = sorted(values)
    if not s:
        return 0.0
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def sensitivity(
    district: District, params: ScenarioParams, plan: list[PlanAction], seed: int, samples: int
) -> dict[str, Any]:
    rng = random.Random(seed)
    disruptions: list[float] = []
    req_ok = 0
    for _ in range(samples):
        r = simulate(district, perturbed(params, rng, district), plan)
        disruptions.append(r.summary["disruption_total"])
        req_ok += all(v["satisfied"] for v in r.summary["requirements"].values())
    return {
        "samples": samples,
        "seed": seed,
        "disruption_p10": round(_pct(disruptions, 0.1), 2),
        "disruption_p50": round(_pct(disruptions, 0.5), 2),
        "disruption_p90": round(_pct(disruptions, 0.9), 2),
        "requirements_met_fraction": round(req_ok / samples, 3) if samples else None,
        "perturbation": PERTURBATION,
        "label": "scenario sensitivity (seeded perturbations), not a calibrated probability",
    }
