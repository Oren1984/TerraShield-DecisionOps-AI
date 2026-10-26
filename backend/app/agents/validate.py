"""Validation of agent outputs against schemas, evidence, permitted assets and actions."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from app.agents.evidence import evidence_for_role
from app.agents.schemas import (
    ROLE_ACTIONS,
    CoordinatorConsolidation,
    PlanExplanation,
    SpecialistAssessment,
)
from app.simulation.actions import CATALOG, valid_targets
from app.simulation.district import District
from app.simulation.scenario import ScenarioParams


@dataclass
class ValidationContext:
    district: District
    params: ScenarioParams
    evidence: list[dict[str, Any]]
    retrieved: dict[str, list[str]] = field(default_factory=dict)  # role -> permitted chunk ids
    conflict_ids: set[str] = field(default_factory=set)
    proposed_keys: set[str] = field(default_factory=set)
    verified_plan_labels: set[str] = field(default_factory=set)

    def evidence_ids(self, role: str) -> set[str]:
        return {e["id"] for e in evidence_for_role(self.evidence, role)}


def _parse(raw: Any, model: type[BaseModel]) -> tuple[Any, list[str]]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            return None, [f"invalid_json: {exc.msg} at position {exc.pos}"]
    if not isinstance(raw, dict):
        return None, ["invalid_json: top-level value must be an object"]
    try:
        return model.model_validate(raw), []
    except ValidationError as exc:
        return None, [f"schema: {'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:10]]


def _check_citations(cites: list[str], permitted: list[str], errors: list[str]) -> None:
    for c in cites:
        if c not in permitted:
            errors.append(f"citation_not_permitted: {c}")


def validate_specialist(raw: Any, role: str, ctx: ValidationContext) -> tuple[SpecialistAssessment | None, list[str]]:
    out, errors = _parse(raw, SpecialistAssessment)
    if out is None:
        return None, errors
    if out.role != role:
        errors.append(f"role_mismatch: expected {role}, got {out.role}")
    known = ctx.evidence_ids(role)
    d = ctx.district
    ids: list[str] = []
    for f in out.findings:
        ids += f.evidence_ids
        errors += [f"unknown_asset: {a}" for a in f.asset_ids if a not in d.assets]
        errors += [f"unknown_edge: {e}" for e in f.edge_ids if e not in d.edges]
    for a in out.proposed_actions:
        ids += a.evidence_ids
        if a.action_type not in CATALOG:
            errors.append(f"unknown_action_type: {a.action_type}")
        elif a.action_type not in ROLE_ACTIONS[role]:
            errors.append(f"action_not_permitted_for_role: {a.action_type}")
        elif a.target not in valid_targets(d, a.action_type):
            errors.append(f"invalid_target: {a.action_type} -> {a.target}")
    for r in out.restrictions:
        ids += r.evidence_ids
        if role != "ground":
            errors.append("restriction_not_permitted_for_role")
        if r.edge_id not in d.edges:
            errors.append(f"unknown_edge: {r.edge_id}")
        elif not any(
            eid in (f"ev-edge-{r.edge_id}-ground-si", f"ev-edge-{r.edge_id}-closure") for eid in r.evidence_ids
        ):
            errors.append(f"restriction_without_edge_evidence: {r.edge_id}")
        if r.from_hour >= ctx.params.duration_h:
            errors.append(f"restriction_hour_out_of_range: {r.from_hour}")
    errors += [f"unknown_evidence_id: {i}" for i in sorted(set(ids)) if i not in known]
    _check_citations(out.citations, ctx.retrieved.get(role, []), errors)
    return (out if not errors else None), errors


def validate_consolidation(raw: Any, ctx: ValidationContext) -> tuple[CoordinatorConsolidation | None, list[str]]:
    out, errors = _parse(raw, CoordinatorConsolidation)
    if out is None:
        return None, errors
    for r in out.resolutions:
        if r.conflict_id not in ctx.conflict_ids:
            errors.append(f"unknown_conflict_id: {r.conflict_id}")
    for q in out.revision_requests:
        if q.conflict_id not in ctx.conflict_ids:
            errors.append(f"unknown_conflict_id: {q.conflict_id}")
    for s in out.selected_actions:
        key = f"{s.action_type}:{s.target}"
        if key not in ctx.proposed_keys:
            errors.append(f"selected_action_not_proposed: {key}")
    _check_citations(out.citations, ctx.retrieved.get("coordinator", []), errors)
    return (out if not errors else None), errors


def validate_explanation(raw: Any, ctx: ValidationContext) -> tuple[PlanExplanation | None, list[str]]:
    out, errors = _parse(raw, PlanExplanation)
    if out is None:
        return None, errors
    allowed = ctx.verified_plan_labels | ({"none"} if not ctx.verified_plan_labels else set())
    if out.recommended_plan not in allowed:
        errors.append(f"recommended_plan_not_verified: {out.recommended_plan}")
    known = ctx.evidence_ids("coordinator")
    errors += [f"unknown_evidence_id: {i}" for i in out.evidence_ids if i not in known]
    _check_citations(out.citations, ctx.retrieved.get("coordinator", []), errors)
    return (out if not errors else None), errors
