"""Simulator: determinism, conservation, units, boundaries and monotonic checks."""

from __future__ import annotations

import random

import pytest

from app.simulation.engine import PlanAction, simulate
from app.simulation.scenario import ScenarioParams, hazard_series
from app.simulation.sensitivity import perturbed, sensitivity
from tests.conftest import load_scenario


@pytest.mark.parametrize("prefix", ["01", "02", "03", "04"])
def test_deterministic_and_mass_balance(district, prefix):
    _, p = load_scenario(prefix)
    a = simulate(district, p)
    b = simulate(district, p)
    assert a.to_dict() == b.to_dict()
    bal = a.balance
    assert abs(bal["residual_m3"]) < 1e-3
    assert bal["total_inflow_m3"] >= bal["total_outflow_m3"] >= 0


def test_mass_balance_under_random_perturbations(district, coastal):
    rng = random.Random(7)
    for _ in range(25):
        r = simulate(district, perturbed(coastal, rng, district))
        assert abs(r.balance["residual_m3"]) < 1e-3


def test_hazard_series_units_and_lengths(coastal):
    hz = hazard_series(coastal)
    for key in ("rain_mm_h", "sea_level_m", "wind_ms", "gust_ms"):
        assert len(hz[key]) == coastal.duration_h
    assert min(hz["rain_mm_h"]) >= 0
    assert all(g >= w for g, w in zip(hz["gust_ms"], hz["wind_ms"], strict=True))


def test_depth_is_storage_over_ponding_area(district, coastal):
    r = simulate(district, coastal)
    z = district.zones["Z1"]
    for h in r.hours:
        zo = h["zones"]["Z1"]
        assert zo["depth_m"] == pytest.approx(zo["storage_m3"] / (z.area_m2 * z.ponding_fraction), abs=1e-3)
        assert zo["level_m"] == pytest.approx(z.base_elev_m + zo["depth_m"], abs=1e-3)


def test_calm_boundary_has_no_damage(district):
    p = ScenarioParams.model_validate(
        {
            "duration_h": 12,
            "rainfall": {"base_mm_h": 0, "peak_mm_h": 0},
            "coastal": {"mean_level_m": 0.5, "tide_amplitude_m": 0.2, "surge_peak_m": 0},
            "wind": {"base_ms": 3, "peak_ms": 0},
            "soil_saturation": {"Z1": 0.3, "Z2": 0.3, "Z3": 0.3, "Z4": 0.3},
        }
    )
    r = simulate(district, p)
    assert r.summary["failed_assets"] == {}
    assert r.summary["disruption_total"] == 0
    assert all(not h["closed_edges"] for h in r.hours)
    assert r.balance["total_inflow_m3"] == 0


def test_more_protection_never_worsens_isolated_asset(district, coastal):
    """Monotonic check in a fixed case: none <= sandbag <= barrier for the protected asset."""

    def peak(plan):
        r = simulate(district, coastal, plan)
        return max(h["assets"]["A1"]["flood_depth_m"] for h in r.hours)

    none = peak([])
    sand = peak([PlanAction("sandbag_asset", "A1", 1)])
    barrier = peak([PlanAction("flood_barrier_asset", "A1", 1)])
    assert none >= sand >= barrier


def test_more_rain_never_lowers_peak_depth(district, coastal):
    lo = coastal.model_copy(deep=True)
    hi = coastal.model_copy(deep=True)
    hi.rainfall.peak_mm_h = lo.rainfall.peak_mm_h + 10
    assert simulate(district, hi).summary["peak_depth_m"]["Z1"] >= simulate(district, lo).summary["peak_depth_m"]["Z1"]


def test_extra_pumping_never_raises_depth(district, coastal):
    base = simulate(district, coastal).summary["peak_depth_m"]["Z1"]
    plan = [PlanAction("clear_drains", "Z1", 1), PlanAction("temporary_pump", "Z1", 4)]
    assert simulate(district, coastal, plan).summary["peak_depth_m"]["Z1"] <= base


def test_cascade_and_generator(district, cascade):
    r = simulate(district, cascade)
    fail_h = r.summary["failed_assets"]["A3"]["hour"]
    assert r.hours[fail_h]["assets"]["A7"]["state"] == "unpowered"
    with_gen = simulate(district, cascade, [PlanAction("backup_generator", "A7", 1)])
    assert all(h["assets"]["A7"]["state"] != "unpowered" for h in with_gen.hours)


def test_ground_closure_persists_and_restrictions_apply(district, windy):
    r = simulate(district, windy)
    first = r.summary["first_closure"]["E2"]
    assert first["cause"] == "ground"
    assert all(h["closed_edges"].get("E2") == "ground" for h in r.hours[first["hour"] :])
    restricted = simulate(district, windy, None, {"E11": 3})
    assert all(h["closed_edges"].get("E11") == "restricted" for h in restricted.hours[3:])
    assert "E11" not in restricted.hours[2]["closed_edges"]


def test_seeded_scenarios_qualitative_behaviour(district, coastal, windy, cascade, conflict):
    s1 = simulate(district, coastal).summary
    assert {"A1", "A2"} <= set(s1["failed_assets"])
    s2 = simulate(district, windy).summary
    assert s2["failed_assets"]["A8"]["cause"] == "wind"
    assert not s2["requirements"]["A8"]["satisfied"]
    s3 = simulate(district, cascade).summary
    assert s3["failed_assets"]["A3"]["hour"] <= s3["failed_assets"]["A2"]["hour"]
    s4 = simulate(district, conflict).summary
    assert not s4["requirements"]["A2"]["satisfied"]


def test_sensitivity_is_seeded(district, coastal):
    a = sensitivity(district, coastal, [], seed=11, samples=6)
    b = sensitivity(district, coastal, [], seed=11, samples=6)
    assert a == b
    assert a["disruption_p10"] <= a["disruption_p50"] <= a["disruption_p90"]
    assert "not a calibrated probability" in a["label"]
