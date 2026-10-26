"""Shared structured contracts for all agent roles and both providers.

``extra="forbid"`` everywhere: an output that adds fields (for example an
"approved" flag) is rejected rather than silently ignored.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["water", "air", "ground"]
Severity = Literal["low", "moderate", "high", "critical"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Finding(_Strict):
    statement: str = Field(..., min_length=1, max_length=400)
    severity: Severity
    evidence_ids: list[str] = Field(..., min_length=1, max_length=6)
    asset_ids: list[str] = Field(default_factory=list, max_length=10)
    edge_ids: list[str] = Field(default_factory=list, max_length=10)


class ProposedAction(_Strict):
    action_type: str = Field(..., max_length=40)
    target: str = Field(..., max_length=16)
    rationale: str = Field(..., min_length=1, max_length=400)
    evidence_ids: list[str] = Field(..., min_length=1, max_length=6)
    priority: int = Field(..., ge=1, le=5)


class Restriction(_Strict):
    kind: Literal["road_closure"]
    edge_id: str = Field(..., max_length=16)
    from_hour: int = Field(..., ge=0, le=48)
    reason: str = Field(..., min_length=1, max_length=300)
    evidence_ids: list[str] = Field(..., min_length=1, max_length=4)


class SpecialistAssessment(_Strict):
    role: Role
    summary: str = Field(..., min_length=1, max_length=800)
    findings: list[Finding] = Field(default_factory=list, max_length=8)
    proposed_actions: list[ProposedAction] = Field(default_factory=list, max_length=8)
    restrictions: list[Restriction] = Field(default_factory=list, max_length=6)
    citations: list[str] = Field(default_factory=list, max_length=6)
    insufficient_evidence: bool = False


class NonGroundAssessment(_Strict):
    """Output schema offered to water and air: identical but without restrictions,
    which only the ground role may issue. Validation still uses SpecialistAssessment."""

    role: Role
    summary: str = Field(..., min_length=1, max_length=800)
    findings: list[Finding] = Field(default_factory=list, max_length=8)
    proposed_actions: list[ProposedAction] = Field(default_factory=list, max_length=8)
    citations: list[str] = Field(default_factory=list, max_length=6)
    insufficient_evidence: bool = False


def specialist_schema(role: str) -> type[BaseModel]:
    return SpecialistAssessment if role == "ground" else NonGroundAssessment


class ConflictResolution(_Strict):
    conflict_id: str = Field(..., max_length=16)
    resolution: Literal[
        "drop_action", "reschedule", "substitute", "accept_restriction", "planner_selects", "escalate_to_owner"
    ]
    explanation: str = Field(..., min_length=1, max_length=400)


class RevisionRequest(_Strict):
    role: Role
    conflict_id: str = Field(..., max_length=16)
    request: str = Field(..., min_length=1, max_length=400)


class SelectedAction(_Strict):
    action_type: str = Field(..., max_length=40)
    target: str = Field(..., max_length=16)


class CoordinatorConsolidation(_Strict):
    summary: str = Field(..., min_length=1, max_length=900)
    resolutions: list[ConflictResolution] = Field(default_factory=list, max_length=12)
    revision_requests: list[RevisionRequest] = Field(default_factory=list, max_length=3)
    selected_actions: list[SelectedAction] = Field(default_factory=list, max_length=16)
    citations: list[str] = Field(default_factory=list, max_length=6)


class PlanExplanation(_Strict):
    recommended_plan: str = Field(..., max_length=40)
    explanation: str = Field(..., min_length=1, max_length=1200)
    tradeoffs: list[str] = Field(default_factory=list, max_length=5)
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)
    citations: list[str] = Field(default_factory=list, max_length=6)
    insufficient_evidence: bool = False


ROLE_ACTIONS: dict[str, set[str]] = {
    "water": {
        "sandbag_asset",
        "flood_barrier_asset",
        "temporary_flood_wall",
        "temporary_pump",
        "clear_drains",
        "backup_generator",
    },
    "air": {"brace_structure", "secure_mast", "mobile_comms_unit"},
    "ground": {"slope_drainage"},
}
"""Per-role action allowlist (subset of the catalog)."""
