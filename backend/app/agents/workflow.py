"""Shared agent workflow used by both Mock and Local modes.

A small explicit sequence; agents never schedule work, authorize anything,
compute physics or approve plans. Every step writes trace events that record
what actually happened (inputs used, outputs, validation results), not model
reasoning.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.agents.conflicts import accepted_restrictions, detect_conflicts
from app.agents.evidence import build_evidence, evidence_for_role
from app.agents.providers import MockProvider, OllamaProvider, Provider, ProviderError
from app.agents.retrieval import FIXTURE_MODEL_ID, ollama_model_id, retrieve
from app.agents.schemas import (
    ROLE_ACTIONS,
    CoordinatorConsolidation,
    PlanExplanation,
    SpecialistAssessment,
)
from app.agents.validate import (
    ValidationContext,
    validate_consolidation,
    validate_explanation,
    validate_specialist,
)
from app.audit import trace
from app.config import settings
from app.jobs.statemachine import transition
from app.models import CandidatePlanRow, ConstraintResult, Run
from app.models import SpecialistAssessment as AssessmentRow
from app.planning.planner import RANKING, PlanningOutcome, plan_scenario
from app.simulation.actions import CATALOG, valid_targets
from app.simulation.district import District, load_district
from app.simulation.scenario import ScenarioParams

SPECIALISTS = ("water", "air", "ground")


def _validate_specialist(raw: Any, role: str, ctx: ValidationContext) -> tuple[Any, list[str]]:
    return validate_specialist(raw, role, ctx)


ROLE_TOPICS = {
    "water": "flood protection sandbags barriers pump stations drainage outfalls surge power dependencies",
    "air": "wind working limits elevated work mast bracing substitute mobile communications exposed roads",
    "ground": "slope susceptibility index soil saturation road restriction levels slope drainage",
    "coordinator": "coordination authority resolving conflicts restrictions hard constraints approval",
}


class RunCancelled(Exception):
    pass


class RunFailed(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def make_provider(mode: str, seed: int) -> Provider:
    return OllamaProvider(seed) if mode == "local" else MockProvider(seed)


def embedding_model_for(mode: str) -> str:
    return ollama_model_id() if mode == "local" else FIXTURE_MODEL_ID


class Workflow:
    def __init__(
        self, db: Session, run: Run, provider: Provider | None = None, checkpoint: Callable[[], None] | None = None
    ):
        self.db = db
        self.run = run
        self.district: District = load_district()
        self.params = ScenarioParams.model_validate(run.inputs["params"])
        self.provider = provider or make_provider(run.mode, run.seed)
        self.started = time.monotonic()
        self._checkpoint = checkpoint

    # -- helpers -----------------------------------------------------------------
    def check(self) -> None:
        """Cancellation, time limit and lease heartbeat between steps."""
        self.db.commit()
        self.db.refresh(self.run)
        if self.run.cancel_requested:
            raise RunCancelled()
        if time.monotonic() - self.started > settings.run_max_seconds:
            raise RunFailed("run_timeout", f"analysis exceeded {settings.run_max_seconds:.0f}s")
        if self._checkpoint:
            self._checkpoint()

    def t(self, actor: str, kind: str, message: str = "", **payload: Any) -> None:
        trace(self.db, self.run.id, actor, kind, message, **payload)

    def _allowed_actions(self, role: str) -> list[dict[str, Any]]:
        out = []
        for t in sorted(ROLE_ACTIONS[role]):
            s = CATALOG[t]
            targets = valid_targets(self.district, t)
            out.append(
                {
                    "type": t,
                    "label": s.label,
                    "description": s.description,
                    "valid_targets": targets,
                    "wind_limit_ms": s.wind_limit_ms,
                    "duration_h": s.duration_h,
                    "crews": s.crews,
                    "equipment": s.equipment,
                    "cost": s.cost,
                }
            )
        return out

    def _retrieve(self, role: str, query: str) -> list[dict[str, Any]]:
        model_id = embedding_model_for(self.run.mode)
        res = retrieve(self.db, self.run.workspace_id, role, query, model_id)
        self.t(
            "retrieval",
            "evidence_retrieved",
            f"{role}: {len(res.chunks)} chunk(s) retrieved ({res.status}).",
            role=role,
            model_id=model_id,
            status=res.status,
            chunks=[
                {"chunk_id": c.chunk_id, "similarity": c.similarity, "version": c.document_version} for c in res.chunks
            ],
        )
        return [
            {
                "chunk_id": c.chunk_id,
                "title": c.title,
                "heading": c.heading,
                "text": c.text,
                "similarity": c.similarity,
                "document_version": c.document_version,
            }
            for c in res.chunks
        ]

    def _call(
        self,
        role: str,
        step: str,
        round_: int,
        payload: dict[str, Any],
        schema: Any,
        validator: Callable[[Any], tuple[Any, list[str]]],
        retrieved: list[str],
    ) -> Any:
        errors: list[str] | None = None
        last_raw: Any = None
        total_ms = 0
        attempts = 0
        self.t(
            role,
            "agent_call_started",
            f"{role} {step} (round {round_}) via {self.provider.name}.",
            role=role,
            step=step,
            round=round_,
            provider=self.provider.name,
            model_id=self.provider.model_id,
        )
        for attempt in range(1, settings.agent_max_retries + 2):
            attempts = attempt
            try:
                gen = self.provider.generate(role, step, payload, schema, errors)
            except ProviderError as exc:
                self._store(
                    role, step, round_, None, [f"{exc.code}: {exc.detail}"], retrieved, attempts, total_ms, "invalid"
                )
                self.t(
                    role, "agent_call_failed", f"{role} {step} failed: {exc.code}.", code=exc.code, detail=exc.detail
                )
                self.db.commit()  # keep the failure record; the caller rolls back only uncommitted work
                raise RunFailed(exc.code, f"{role} {step}: {exc.detail}") from exc
            total_ms += gen.latency_ms
            last_raw = gen.raw
            parsed, errors = validator(gen.raw)
            if parsed is not None:
                self._store(
                    role, step, round_, parsed.model_dump(mode="json"), [], retrieved, attempts, total_ms, "valid"
                )
                self.t(
                    role,
                    "agent_output_validated",
                    f"{role} {step} output passed validation.",
                    role=role,
                    step=step,
                    attempt=attempt,
                    latency_ms=gen.latency_ms,
                    output_tokens=gen.output_tokens,
                    prompt_tokens=gen.prompt_tokens,
                )
                return parsed
            self.t(
                "validator",
                "agent_output_rejected",
                f"{role} {step} output rejected ({len(errors)} issue(s)).",
                role=role,
                step=step,
                attempt=attempt,
                errors=errors[:10],
            )
        excerpt = last_raw if isinstance(last_raw, str) else json.dumps(last_raw, default=str)
        self._store(
            role,
            step,
            round_,
            {"invalid_output_excerpt": excerpt[:2000]},
            errors or [],
            retrieved,
            attempts,
            total_ms,
            "invalid",
        )
        self.db.commit()
        raise RunFailed("agent_output_invalid", f"{role} {step}: {'; '.join((errors or [])[:3])}")

    def _store(
        self,
        role: str,
        step: str,
        round_: int,
        output: Any,
        errors: list[str],
        retrieved: list[str],
        attempts: int,
        latency_ms: int,
        status: str,
    ) -> None:
        self.db.add(
            AssessmentRow(
                run_id=self.run.id,
                revision=self.run.revision,
                round=round_,
                role=role,
                step=step,
                provider=self.provider.name,
                model_id=self.provider.model_id,
                status=status,
                output=output,
                validation_errors=errors,
                retrieved_chunk_ids=retrieved,
                attempts=attempts,
                latency_ms=latency_ms,
            )
        )

    # -- analysis ---------------------------------------------------------------
    def analyze(self) -> None:
        run = self.run
        if not transition(
            self.db,
            run.id,
            {"queued", "assessing", "planning"},
            "assessing",
            "worker",
            "Assessment started.",
            started_at=run.started_at or datetime.now(UTC),
        ):
            raise RunCancelled()
        # Restart from a clean slate for this revision (idempotent re-execution after a crash).
        self.db.execute(
            delete(AssessmentRow).where(AssessmentRow.run_id == run.id, AssessmentRow.revision == run.revision)
        )
        self.db.execute(
            delete(CandidatePlanRow).where(CandidatePlanRow.run_id == run.id, CandidatePlanRow.revision == run.revision)
        )
        self.check()

        from app.simulation.engine import simulate

        baseline = simulate(self.district, self.params)
        evidence = build_evidence(self.district, self.params, baseline)
        run.evidence = {"items": evidence, "simulator": run.simulator_version}
        self.t(
            "simulator",
            "baseline_simulated",
            "No-action baseline simulated.",
            disruption_total=baseline.summary["disruption_total"],
            failed_assets=baseline.summary["failed_assets"],
            mass_balance_residual_m3=baseline.balance["residual_m3"],
        )
        self.t(
            "simulator",
            "evidence_built",
            f"{len(evidence)} evidence items derived from the simulation.",
            count=len(evidence),
        )
        self.check()

        ctx = ValidationContext(self.district, self.params, evidence)
        crit = {a.id: a.criticality for a in self.district.assets.values()}
        notes = self.params.notes
        assessments: dict[str, SpecialistAssessment] = {}
        payloads: dict[str, dict[str, Any]] = {}
        for role in SPECIALISTS:
            role_ev = evidence_for_role(evidence, role)
            query = ROLE_TOPICS[role] + ". " + " ".join(e["statement"] for e in role_ev[:3])
            guidance = self._retrieve(role, query)
            ctx.retrieved[role] = [g["chunk_id"] for g in guidance]
            payloads[role] = {
                "role": role,
                "scenario": {"title": run.inputs.get("title", ""), "duration_h": self.params.duration_h},
                "evidence": role_ev,
                "allowed_actions": self._allowed_actions(role),
                "resources": self.params.resources.model_dump(),
                "criticality": crit,
                "zones": [
                    {"id": z.id, "name": z.name, "downstream": [t for t, _ in z.downstream]}
                    for z in self.district.zones.values()
                ],
                "guidance": [{k: g[k] for k in ("chunk_id", "title", "heading", "text")} for g in guidance],
                "scenario_notes_untrusted": notes,
            }
            assessments[role] = self._call(
                role,
                "assess",
                1,
                payloads[role],
                SpecialistAssessment,
                partial(_validate_specialist, role=role, ctx=ctx),
                ctx.retrieved[role],
            )
            self.check()

        coord_guidance = self._retrieve("coordinator", ROLE_TOPICS["coordinator"])
        ctx.retrieved["coordinator"] = [g["chunk_id"] for g in coord_guidance]
        consolidation = self._consolidate(
            ctx, assessments, evidence, coord_guidance, round_=1, allow_revisions=settings.max_revision_rounds > 0
        )
        rounds = 1
        while consolidation[0].revision_requests and rounds <= settings.max_revision_rounds:
            rounds += 1
            conflict_by_id = {c["id"]: c for c in consolidation[1]}
            by_role: dict[str, list[dict[str, Any]]] = {}
            for rq in consolidation[0].revision_requests:
                c = conflict_by_id.get(rq.conflict_id, {})
                by_role.setdefault(rq.role, []).append(
                    {
                        "conflict_id": rq.conflict_id,
                        "request": rq.request,
                        "action": c.get("action"),
                        "type": c.get("type"),
                    }
                )
            for role, reqs in by_role.items():
                self.t(
                    "coordinator",
                    "revision_requested",
                    f"Coordinator asked {role} to revise ({len(reqs)} item(s)).",
                    role=role,
                    requests=reqs,
                )
                payload = dict(
                    payloads[role], previous=assessments[role].model_dump(mode="json"), revision_requests=reqs
                )
                assessments[role] = self._call(
                    role,
                    "revise",
                    rounds,
                    payload,
                    SpecialistAssessment,
                    partial(_validate_specialist, role=role, ctx=ctx),
                    ctx.retrieved[role],
                )
                self.check()
            consolidation = self._consolidate(
                ctx,
                assessments,
                evidence,
                coord_guidance,
                round_=rounds,
                allow_revisions=rounds <= settings.max_revision_rounds,
            )
        final, conflicts = consolidation
        if final.revision_requests:
            self.t(
                "coordinator",
                "revision_limit_reached",
                "Revision limit reached; remaining requests are recorded but not executed.",
                pending=[r.model_dump() for r in final.revision_requests],
            )
        restrictions = accepted_restrictions(assessments)
        run.evidence = dict(
            run.evidence,
            restrictions=restrictions,
            conflicts=conflicts,
            candidate_actions=[f"{s.action_type}:{s.target}" for s in final.selected_actions],
        )
        if not transition(self.db, run.id, {"assessing"}, "planning", "worker", "Planning started."):
            raise RunCancelled()
        self.check()
        self.plan(ctx, coord_guidance)

    def _consolidate(
        self,
        ctx: ValidationContext,
        assessments: dict[str, SpecialistAssessment],
        evidence: list[dict[str, Any]],
        guidance: list[dict[str, Any]],
        round_: int,
        allow_revisions: bool,
    ) -> tuple[CoordinatorConsolidation, list[dict[str, Any]]]:
        from app.simulation.engine import simulate

        baseline = simulate(self.district, self.params)
        conflicts = detect_conflicts(self.district, self.params, baseline, assessments)
        for c in conflicts:
            self.t("coordinator", "conflict_detected", c["detail"], **c)
        ctx.conflict_ids = {c["id"] for c in conflicts}
        proposals = [
            dict(p.model_dump(mode="json"), role=role) for role, a in assessments.items() for p in a.proposed_actions
        ]
        ctx.proposed_keys = {f"{p['action_type']}:{p['target']}" for p in proposals}
        payload = {
            "evidence": evidence_for_role(evidence, "coordinator"),
            "conflicts": conflicts,
            "proposals": proposals,
            "restrictions": [r.model_dump() for r in assessments["ground"].restrictions]
            if "ground" in assessments
            else [],
            "allow_revisions": allow_revisions,
            "guidance": [{k: g[k] for k in ("chunk_id", "title", "heading", "text")} for g in guidance],
            "scenario_notes_untrusted": self.params.notes,
        }
        out = self._call(
            "coordinator",
            "consolidate",
            round_,
            payload,
            CoordinatorConsolidation,
            lambda raw: validate_consolidation(raw, ctx),
            ctx.retrieved.get("coordinator", []),
        )
        if not allow_revisions and out.revision_requests:
            out = out.model_copy(update={"revision_requests": []})
        self.check()
        return out, conflicts

    # -- planning ----------------------------------------------------------------
    def plan(self, ctx: ValidationContext | None = None, guidance: list[dict[str, Any]] | None = None) -> None:
        run = self.run
        ev = run.evidence or {}
        evidence = ev.get("items", [])
        if ctx is None:
            ctx = ValidationContext(self.district, self.params, evidence)
        if guidance is None:
            guidance = self._retrieve("coordinator", ROLE_TOPICS["coordinator"])
            ctx.retrieved["coordinator"] = [g["chunk_id"] for g in guidance]
        rc = run.revision_constraints or {}
        restrictions = {k: int(v) for k, v in ev.get("restrictions", {}).items()}
        candidate_keys = ev.get("candidate_actions", [])
        self.t(
            "planner",
            "planning_started",
            f"Planning over {len(candidate_keys)} coordinator-selected action(s) with {len(restrictions)} restriction(s).",
            candidate_actions=candidate_keys,
            restrictions=restrictions,
            excluded_actions=rc.get("excluded_actions", []),
            budget_cap=rc.get("budget_cap"),
        )
        outcome = plan_scenario(
            self.district,
            self.params,
            run.seed,
            candidate_keys=candidate_keys,
            excluded_keys=rc.get("excluded_actions"),
            budget_cap=rc.get("budget_cap"),
            restrictions=restrictions,
        )
        for e in outcome.events:
            kind = e.pop("kind")
            self.t("planner", kind, _event_message(kind, e), **e)
        self._store_plans(outcome)
        run.baseline = {
            "summary": outcome.baseline.summary,
            "balance": outcome.baseline.balance,
            "hazards": outcome.baseline.hazards,
            "hours": _compact_hours(outcome.baseline.hours),
        }
        self.check()

        rows = list(
            self.db.scalars(
                select(CandidatePlanRow).where(
                    CandidatePlanRow.run_id == run.id, CandidatePlanRow.revision == run.revision
                )
            )
        )
        ctx.verified_plan_labels = {r.label for r in rows if r.status == "verified"}
        plans_view = [
            {
                "label": r.label,
                "title": r.title,
                "status": r.status,
                "rank": r.rank,
                "objective": r.objective,
                "actions": r.actions,
            }
            for r in rows
        ]
        payload = {
            "plans": plans_view,
            "ranking_rule": RANKING["description"],
            "blocked_reason": outcome.blocked_reason,
            "evidence": evidence_for_role(evidence, "coordinator"),
            "guidance": [{k: g[k] for k in ("chunk_id", "title", "heading", "text")} for g in guidance],
        }
        explanation = self._call(
            "coordinator",
            "explain",
            1,
            payload,
            PlanExplanation,
            lambda raw: validate_explanation(raw, ctx),
            ctx.retrieved.get("coordinator", []),
        )
        run.evidence = dict(run.evidence or {}, explanation=explanation.model_dump(mode="json"))
        run.analysis_seconds = round(time.monotonic() - self.started, 3)
        if outcome.blocked:
            transition(
                self.db,
                run.id,
                {"planning"},
                "blocked",
                "verifier",
                outcome.blocked_reason or "Blocked.",
                blocked_reason=outcome.blocked_reason,
            )
        else:
            transition(
                self.db,
                run.id,
                {"planning"},
                "awaiting_review",
                "planner",
                "Verified plans are ready for owner review.",
            )
        self.db.commit()

    def _store_plans(self, outcome: PlanningOutcome) -> None:
        ranked = {p.label: i + 1 for i, p in enumerate(outcome.ranked())}
        for p in outcome.plans:
            sim = p.verification.sim
            row = CandidatePlanRow(
                run_id=self.run.id,
                revision=self.run.revision,
                label=p.label,
                title=p.title,
                method=p.method,
                solver_status=p.solver_status,
                status=p.status,
                rank=ranked.get(p.label),
                actions=[a.as_dict() | {"label": CATALOG[a.type].label} for a in p.actions],
                objective=p.objective(),
                plan_hash=p.plan_hash(),
                outcome={
                    "summary": sim.summary if sim else None,
                    "disruption_by_hour": [h["disruption"] for h in sim.hours] if sim else [],
                },
                sensitivity=p.sensitivity,
                notes=p.notes,
            )
            self.db.add(row)
            self.db.flush()
            for c in p.verification.checks:
                self.db.add(ConstraintResult(plan_id=row.id, constraint=c.constraint, passed=c.passed, detail=c.detail))
            self.t(
                "verifier",
                "plan_verified" if p.status == "verified" else "plan_rejected",
                f"{p.title}: {p.status.replace('_', ' ')}.",
                label=p.label,
                status=p.status,
                violations=[v["constraint"] for v in p.verification.violations()],
                objective=p.objective(),
            )


def _event_message(kind: str, e: dict[str, Any]) -> str:
    if kind == "solver_result":
        return f"CP-SAT {e['label']}: {e['status']}, {e['actions']} action(s), verified={e['verified']}."
    if kind == "planner_iteration":
        return f"Planner iteration {e['iteration']}: {e['options']} scheduling options."
    return kind


def _compact_hours(hours: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-hour state for map playback, without per-edge index noise."""
    out = []
    for h in hours:
        out.append(
            {
                "hour": h["hour"],
                "rain_mm_h": h["rain_mm_h"],
                "sea_level_m": h["sea_level_m"],
                "wind_ms": h["wind_ms"],
                "gust_ms": h["gust_ms"],
                "disruption": h["disruption"],
                "closed_edges": h["closed_edges"],
                "zones": {
                    z: {"depth_m": v["depth_m"], "level_m": v["level_m"], "saturation": v["saturation"]}
                    for z, v in h["zones"].items()
                },
                "assets": {
                    a: {
                        "state": v["state"],
                        "flood_depth_m": v["flood_depth_m"],
                        "reachable": v["reachable"],
                        "causes": v["causes"],
                    }
                    for a, v in h["assets"].items()
                },
                "active_actions": h["active_actions"],
                "completed_actions": h["completed_actions"],
            }
        )
    return out
