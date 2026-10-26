"""Model providers behind one interface.

* ``MockProvider``: seeded, deterministic rules that read the same payload the
  local model receives and emit the same schemas. Labelled ``mock`` everywhere.
* ``OllamaProvider``: real local inference through the Ollama HTTP API with a
  JSON-schema constrained response, bounded tokens, context and time.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from app.agents.prompts import system_prompt, user_prompt
from app.config import settings


class ProviderError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass
class Generation:
    raw: Any
    latency_ms: int
    prompt_tokens: int | None = None
    output_tokens: int | None = None


class Provider(Protocol):
    name: str
    model_id: str

    def generate(
        self, role: str, step: str, payload: dict[str, Any], schema: type[BaseModel], errors: list[str] | None = None
    ) -> Generation: ...


# ---------------------------------------------------------------------------------
# Mock provider


def _ev(payload: dict[str, Any], prefix: str) -> list[dict[str, Any]]:
    return [e for e in payload["evidence"] if e["id"].startswith(prefix)]


def _cites(payload: dict[str, Any], n: int = 2) -> list[str]:
    return [c["chunk_id"] for c in payload.get("guidance", [])[:n]]


class MockProvider:
    name = "mock"

    def __init__(self, seed: int):
        self.seed = seed
        self.model_id = "mock-rules-1.0"

    def generate(
        self, role: str, step: str, payload: dict[str, Any], schema: type[BaseModel], errors: list[str] | None = None
    ) -> Generation:
        t0 = time.perf_counter()
        fn = getattr(self, f"_{role}_{step}")
        out = fn(payload)
        return Generation(raw=out, latency_ms=int((time.perf_counter() - t0) * 1000))

    # --- water
    def _water_assess(self, p: dict[str, Any]) -> dict[str, Any]:
        res = p["resources"]
        crit = p["criticality"]
        findings, actions = [], []
        for e in _ev(p, "ev-zone-"):
            if e["id"].endswith("peak-depth") and e["data"]["value"] > 0.1:
                d = e["data"]["value"]
                findings.append(
                    {
                        "statement": e["statement"],
                        "severity": "high" if d > 0.5 else "moderate",
                        "evidence_ids": [e["id"], "ev-haz-sea-peak"],
                        "asset_ids": [],
                        "edge_ids": [],
                    }
                )
        failures = sorted(
            (e for e in _ev(p, "ev-asset-") if e["id"].endswith("-failure") and e["data"]["cause"] == "flood"),
            key=lambda e: (-crit[e["data"]["asset"]], e["data"]["hour"]),
        )
        barriers = res["barriers"]
        allowed = {a["type"]: set(a["valid_targets"]) for a in p["allowed_actions"]}
        for e in failures:
            aid = e["data"]["asset"]
            findings.append(
                {
                    "statement": e["statement"],
                    "severity": "critical" if crit[aid] >= 4 else "high",
                    "evidence_ids": [e["id"]],
                    "asset_ids": [aid],
                    "edge_ids": [],
                }
            )
            depth_ev = next((x for x in _ev(p, f"ev-asset-{aid}-flood-depth")), None)
            excess = depth_ev["data"]["value"] - depth_ev["data"]["threshold"] if depth_ev else 0.0
            use_barrier = barriers > 0 and excess > 0.25
            t = "flood_barrier_asset" if use_barrier else "sandbag_asset"
            if aid in allowed.get(t, set()):
                barriers -= 1 if use_barrier else 0
                actions.append(
                    {
                        "action_type": t,
                        "target": aid,
                        "priority": 1 if crit[aid] >= 4 else 2,
                        "rationale": f"Protect {aid} before its simulated flood hour {e['data']['hour']}.",
                        "evidence_ids": [e["id"]] + ([depth_ev["id"]] if depth_ev else []),
                    }
                )
        unpowered = sorted(_ev(p, "ev-asset-"), key=lambda e: -crit.get(e["data"].get("asset", ""), 0))
        gens = res["generators"]
        for e in unpowered:
            if e["id"].endswith("-unpowered") and gens > 0:
                aid = e["data"]["asset"]
                if aid in allowed.get("backup_generator", set()) and not any(a["target"] == aid for a in actions):
                    gens -= 1
                    findings.append(
                        {
                            "statement": e["statement"],
                            "severity": "high",
                            "evidence_ids": [e["id"]],
                            "asset_ids": [aid, e["data"]["supplier"]],
                            "edge_ids": [],
                        }
                    )
                    actions.append(
                        {
                            "action_type": "backup_generator",
                            "target": aid,
                            "priority": 2,
                            "rationale": f"Keep {aid} running if supply from {e['data']['supplier']} is lost.",
                            "evidence_ids": [e["id"]],
                        }
                    )
        deep = [e for e in _ev(p, "ev-zone-") if e["id"].endswith("peak-depth") and e["data"]["value"] > 0.3]
        for e in sorted(deep, key=lambda e: -e["data"]["value"])[:1]:
            z = e["data"]["zone"]
            actions.append(
                {
                    "action_type": "clear_drains",
                    "target": z,
                    "priority": 3,
                    "rationale": f"Raise gravity drainage in {z} before the surge blocks outfalls.",
                    "evidence_ids": [e["id"]],
                }
            )
            if res["pumps"] > 0:
                actions.append(
                    {
                        "action_type": "temporary_pump",
                        "target": z,
                        "priority": 3,
                        "rationale": f"Add self-powered pumping in {z}.",
                        "evidence_ids": [e["id"]],
                    }
                )
            for up in p.get("zones", []):
                if z in up["downstream"] and up["id"] in allowed.get("clear_drains", set()):
                    actions.append(
                        {
                            "action_type": "clear_drains",
                            "target": up["id"],
                            "priority": 4,
                            "rationale": f"Clear {up['name']} drains to cut runoff routed into {z}.",
                            "evidence_ids": [e["id"]],
                        }
                    )
        return {
            "role": "water",
            "summary": f"{len(failures)} asset(s) fail by flooding without action; "
            f"{len(actions)} water actions proposed from the allowlist.",
            "findings": findings[:8],
            "proposed_actions": actions[:8],
            "restrictions": [],
            "citations": _cites(p),
            "insufficient_evidence": not findings,
        }

    def _water_revise(self, p: dict[str, Any]) -> dict[str, Any]:
        prev = dict(p["previous"])
        dropped = {r["action"] for r in p["revision_requests"] if r.get("action")}
        keep = [a for a in prev["proposed_actions"] if f"{a['action_type']}:{a['target']}" not in dropped]
        prev["proposed_actions"] = keep
        prev["summary"] = (
            prev["summary"] + f" Revised: withdrew {', '.join(sorted(dropped)) or 'nothing'} "
            "because no permitted route reaches the site in time."
        )[:800]
        return prev

    # --- air
    def _air_assess(self, p: dict[str, Any]) -> dict[str, Any]:
        crit = p["criticality"]
        findings, actions = [], []
        allowed = {a["type"]: set(a["valid_targets"]) for a in p["allowed_actions"]}
        for e in _ev(p, "ev-asset-"):
            if not e["id"].endswith("-gust"):
                continue
            aid = e["data"]["asset"]
            ratio = e["data"]["value"] / e["data"]["threshold"]
            fails = any(x["id"] == f"ev-asset-{aid}-failure" for x in p["evidence"])
            sev = "critical" if fails else ("high" if ratio >= 0.85 else "moderate")
            ids = [e["id"]] + ([f"ev-asset-{aid}-failure"] if fails else [])
            findings.append(
                {"statement": e["statement"], "severity": sev, "evidence_ids": ids, "asset_ids": [aid], "edge_ids": []}
            )
            if ratio >= 0.85:
                t = "secure_mast" if aid in allowed.get("secure_mast", set()) else "brace_structure"
                if aid in allowed.get(t, set()):
                    actions.append(
                        {
                            "action_type": t,
                            "target": aid,
                            "priority": 1 if crit[aid] >= 4 else 2,
                            "rationale": f"Raise wind resistance of {aid} before the gust peak.",
                            "evidence_ids": ids,
                        }
                    )
        for e in _ev(p, "ev-edge-"):
            if e["data"].get("cause") == "wind":
                findings.append(
                    {
                        "statement": e["statement"],
                        "severity": "moderate",
                        "evidence_ids": [e["id"]],
                        "asset_ids": [],
                        "edge_ids": [e["data"]["edge"]],
                    }
                )
        return {
            "role": "air",
            "summary": f"Peak wind evidence reviewed; {len(actions)} wind-protection action(s) proposed.",
            "findings": findings[:8],
            "proposed_actions": actions[:8],
            "restrictions": [],
            "citations": _cites(p),
            "insufficient_evidence": not findings,
        }

    def _air_revise(self, p: dict[str, Any]) -> dict[str, Any]:
        prev = dict(p["previous"])
        allowed = {a["type"]: set(a["valid_targets"]) for a in p["allowed_actions"]}
        infeasible = {r["action"] for r in p["revision_requests"] if r.get("action")}
        keep, notes = [], []
        for a in prev["proposed_actions"]:
            key = f"{a['action_type']}:{a['target']}"
            if key not in infeasible:
                keep.append(a)
                continue
            if (
                a["action_type"] == "secure_mast"
                and p["resources"]["comms_units"] > 0
                and a["target"] in allowed.get("mobile_comms_unit", set())
            ):
                keep.append(
                    {
                        "action_type": "mobile_comms_unit",
                        "target": a["target"],
                        "priority": a["priority"],
                        "rationale": "Mast securing is outside its wind window; substitute a mobile unit deployed from the depot.",
                        "evidence_ids": a["evidence_ids"],
                    }
                )
                notes.append(f"substituted mobile_comms_unit for {key}")
            else:
                notes.append(f"withdrew {key}")
        prev["proposed_actions"] = keep
        prev["summary"] = (prev["summary"] + " Revised: " + ("; ".join(notes) or "no change") + ".")[:800]
        return prev

    # --- ground
    def _ground_assess(self, p: dict[str, Any]) -> dict[str, Any]:
        findings, restrictions, actions = [], [], []
        allowed = {a["type"]: set(a["valid_targets"]) for a in p["allowed_actions"]}
        mob = p["resources"]["mobilization_h"]
        for e in _ev(p, "ev-edge-"):
            if not e["id"].endswith("-ground-si"):
                continue
            eid = e["data"]["edge"]
            r_from = e["data"]["restriction_from"]
            findings.append(
                {
                    "statement": e["statement"],
                    "severity": "high" if r_from is not None else "moderate",
                    "evidence_ids": [e["id"]],
                    "asset_ids": [],
                    "edge_ids": [eid],
                }
            )
            if r_from is not None:
                restrictions.append(
                    {
                        "kind": "road_closure",
                        "edge_id": eid,
                        "from_hour": r_from,
                        "reason": "Slope susceptibility index above the precautionary restriction level.",
                        "evidence_ids": [e["id"]],
                    }
                )
                if r_from >= mob + 4 and eid in allowed.get("slope_drainage", set()):
                    actions.append(
                        {
                            "action_type": "slope_drainage",
                            "target": eid,
                            "priority": 3,
                            "rationale": f"Drainage finished before hour {r_from} could keep {eid} usable.",
                            "evidence_ids": [e["id"]],
                        }
                    )
        return {
            "role": "ground",
            "summary": f"{len(restrictions)} road restriction(s) required for slope safety.",
            "findings": findings[:8],
            "proposed_actions": actions[:8],
            "restrictions": restrictions[:6],
            "citations": _cites(p),
            "insufficient_evidence": not findings,
        }

    def _ground_revise(self, p: dict[str, Any]) -> dict[str, Any]:
        return dict(p["previous"])

    # --- coordinator
    def _coordinator_consolidate(self, p: dict[str, Any]) -> dict[str, Any]:
        resolutions: list[dict[str, Any]] = []
        requests: list[dict[str, Any]] = []
        dropped: set[str] = set()
        allow_revisions = p["allow_revisions"]
        for c in p["conflicts"]:
            t = c["type"]
            if t in ("route_blocked_by_restriction", "route_unavailable", "no_common_window"):
                res = "drop_action"
                dropped.add(c["action"])
                if allow_revisions and len(requests) < 3:
                    requests.append(
                        {
                            "role": c["proposer"],
                            "conflict_id": c["id"],
                            "request": f"{c['action']} cannot start on a permitted route. Withdraw it or offer a reachable alternative.",
                        }
                    )
            elif t == "route_window_narrowed":
                res = "reschedule"
            elif t in ("wind_infeasible", "too_late"):
                res = "substitute" if allow_revisions else "drop_action"
                dropped.add(c["action"])
                if allow_revisions and len(requests) < 3:
                    requests.append(
                        {
                            "role": c["proposer"],
                            "conflict_id": c["id"],
                            "request": f"{c['action']} cannot be completed in time within its working limits. "
                            "Propose a substitute or withdraw it.",
                        }
                    )
            else:
                res = "planner_selects"
            resolutions.append({"conflict_id": c["id"], "resolution": res, "explanation": c["detail"][:400]})
        selected = []
        for prop in p["proposals"]:
            key = f"{prop['action_type']}:{prop['target']}"
            if key not in dropped and {"action_type": prop["action_type"], "target": prop["target"]} not in selected:
                selected.append({"action_type": prop["action_type"], "target": prop["target"]})
        return {
            "summary": f"{len(p['conflicts'])} conflict(s) detected; {len(requests)} revision request(s); "
            f"{len(selected)} candidate action(s) passed to the planner. Ground restrictions are kept as hard constraints.",
            "resolutions": resolutions,
            "revision_requests": requests,
            "selected_actions": selected[:16],
            "citations": _cites(p, 1),
        }

    def _coordinator_explain(self, p: dict[str, Any]) -> dict[str, Any]:
        plans = p["plans"]
        verified = [x for x in plans if x["status"] == "verified" and x["rank"] is not None]
        if not verified:
            return {
                "recommended_plan": "none",
                "explanation": "No plan passed every hard constraint and scenario requirement, so the run is blocked. "
                + (p.get("blocked_reason") or ""),
                "tradeoffs": [],
                "evidence_ids": [e["id"] for e in _ev(p, "ev-req-")][:4],
                "citations": _cites(p, 1),
                "insufficient_evidence": False,
            }
        best = min(verified, key=lambda x: x["rank"])
        base = next((x for x in plans if x["label"] == "baseline"), None)
        o = best["objective"]
        txt = (
            f"{best['title']} ranks first among verified plans: simulated disruption {o['disruption_total']:.1f} "
            f"for cost {o['cost']:.0f}, complete by hour {o['completion_h']}."
        )
        if base:
            txt += f" No action gives {base['objective']['disruption_total']:.1f}."
        tradeoffs = [
            f"{x['title']}: disruption {x['objective']['disruption_total']:.1f}, cost {x['objective']['cost']:.0f}"
            for x in sorted(verified, key=lambda x: x["rank"])[1:4]
        ]
        ev = [e["id"] for e in p["evidence"] if e["id"].endswith("-failure")][:4] + [
            e["id"] for e in _ev(p, "ev-req-")
        ][:2]
        return {
            "recommended_plan": best["label"],
            "explanation": txt[:1200],
            "tradeoffs": tradeoffs,
            "evidence_ids": ev[:8],
            "citations": _cites(p, 2),
            "insufficient_evidence": False,
        }


# ---------------------------------------------------------------------------------
# Ollama provider


class OllamaProvider:
    name = "ollama"

    def __init__(self, seed: int, model: str | None = None, base_url: str | None = None):
        self.seed = seed
        self.model = model or settings.ollama_model
        self.base_url = base_url or settings.ollama_base_url
        self.model_id = f"ollama:{self.model}"

    def generate(
        self, role: str, step: str, payload: dict[str, Any], schema: type[BaseModel], errors: list[str] | None = None
    ) -> Generation:
        prompt_role = "coordinator" if role == "coordinator" else role
        messages = [
            {"role": "system", "content": system_prompt(prompt_role)},
            {"role": "user", "content": user_prompt(f"{role}:{step}", _model_view(payload), errors)},
        ]
        body = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": schema.model_json_schema(),
            "options": {
                "temperature": 0,
                "seed": self.seed,
                "num_predict": settings.agent_max_tokens,
                "num_ctx": settings.agent_context_tokens,
            },
        }
        t0 = time.perf_counter()
        try:
            r = httpx.post(f"{self.base_url}/api/chat", json=body, timeout=settings.agent_timeout_s)
        except httpx.TimeoutException as exc:
            raise ProviderError("model_timeout", f"no response within {settings.agent_timeout_s:.0f}s") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("model_unavailable", str(exc)[:300]) from exc
        if r.status_code == 404:
            raise ProviderError("model_missing", f"model {self.model} is not available in Ollama")
        if r.status_code >= 400:
            raise ProviderError("model_error", f"HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        return Generation(
            raw=data.get("message", {}).get("content", ""),
            latency_ms=int((time.perf_counter() - t0) * 1000),
            prompt_tokens=data.get("prompt_eval_count"),
            output_tokens=data.get("eval_count"),
        )


def _model_view(payload: dict[str, Any]) -> dict[str, Any]:
    """Compact view for the language model: statements instead of raw data."""
    view = {k: v for k, v in payload.items() if k not in ("evidence", "criticality", "proposals_full")}
    view["evidence"] = [{"id": e["id"], "statement": e["statement"]} for e in payload.get("evidence", [])]
    return view


def readiness(base_url: str | None = None) -> dict[str, Any]:
    url = base_url or settings.ollama_base_url
    out: dict[str, Any] = {
        "ollama_url": url,
        "reachable": False,
        "chat_model": settings.ollama_model,
        "embed_model": settings.ollama_embed_model,
        "chat_model_present": False,
        "embed_model_present": False,
    }
    try:
        tags = httpx.get(f"{url}/api/tags", timeout=3).json()
        names = {m.get("name", "") for m in tags.get("models", [])}
        out["reachable"] = True
        out["models"] = sorted(names)

        def present(n: str) -> bool:
            return n in names or f"{n}:latest" in names

        out["chat_model_present"] = present(settings.ollama_model)
        out["embed_model_present"] = present(settings.ollama_embed_model)
    except (httpx.HTTPError, ValueError) as exc:
        out["error"] = str(exc)[:200]
    out["ready"] = out["reachable"] and out["chat_model_present"] and out["embed_model_present"]
    return out
