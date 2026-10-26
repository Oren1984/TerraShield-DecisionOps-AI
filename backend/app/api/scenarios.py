"""Scenarios and immutable scenario versions."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import principal
from app.auth.policy import require, require_workspace
from app.auth.sessions import Principal
from app.db import get_db
from app.models import Run, Scenario, ScenarioVersion
from app.services import current_version, update_scenario
from app.simulation.scenario import ScenarioParams

router = APIRouter(prefix="/api", tags=["scenarios"])


def _get(db: Session, p: Principal, scenario_id: uuid.UUID) -> Scenario:
    sc = db.get(Scenario, scenario_id)
    if sc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Scenario not found.")
    require_workspace(db, p, sc.workspace_id)
    return sc


def scenario_out(db: Session, sc: Scenario, full: bool = False) -> dict:
    ver = current_version(db, sc)
    out = {
        "id": str(sc.id),
        "workspace_id": str(sc.workspace_id),
        "slug": sc.slug,
        "title": sc.title,
        "summary": sc.summary,
        "current_version": sc.current_version,
        "version_id": str(ver.id),
        "seed": ver.seed,
        "input_hash": ver.input_hash,
        "updated_at": sc.updated_at.isoformat(),
        "affected_assets": ver.params.get("affected_assets", []),
        "must_keep_operational": ver.params.get("must_keep_operational", []),
    }
    if full:
        out["params"] = ver.params
        out["template"] = sc.template
        out["versions"] = [
            {
                "version": v.version,
                "id": str(v.id),
                "change_note": v.change_note,
                "created_at": v.created_at.isoformat(),
                "input_hash": v.input_hash,
            }
            for v in db.scalars(
                select(ScenarioVersion)
                .where(ScenarioVersion.scenario_id == sc.id)
                .order_by(ScenarioVersion.version.desc())
            )
        ]
    return out


@router.get("/workspaces/{workspace_id}/scenarios")
def list_scenarios(
    workspace_id: uuid.UUID, p: Principal = Depends(principal), db: Session = Depends(get_db)
) -> list[dict]:
    require_workspace(db, p, workspace_id)
    rows = db.scalars(select(Scenario).where(Scenario.workspace_id == workspace_id).order_by(Scenario.slug))
    return [scenario_out(db, sc) for sc in rows]


@router.get("/scenarios/{scenario_id}")
def get_scenario(scenario_id: uuid.UUID, p: Principal = Depends(principal), db: Session = Depends(get_db)) -> dict:
    return scenario_out(db, _get(db, p, scenario_id), full=True)


class ScenarioEdit(BaseModel):
    params: ScenarioParams
    seed: int | None = Field(None, ge=0, le=2**31 - 1)
    change_note: str = Field("", max_length=500)
    title: str | None = Field(None, max_length=160)
    expected_version: int = Field(..., ge=1, description="Optimistic concurrency: the version being edited")


@router.put("/scenarios/{scenario_id}")
def edit_scenario(
    scenario_id: uuid.UUID, body: ScenarioEdit, p: Principal = Depends(principal), db: Session = Depends(get_db)
) -> dict:
    require(p, "scenarios.write")
    sc = _get(db, p, scenario_id)
    if sc.current_version != body.expected_version:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Scenario is at version {sc.current_version}; reload before editing."
        )
    update_scenario(db, p, sc, body.params, body.seed, body.change_note, body.title)
    db.commit()
    return scenario_out(db, sc, full=True)


@router.get("/scenarios/{scenario_id}/runs")
def scenario_runs(
    scenario_id: uuid.UUID, p: Principal = Depends(principal), db: Session = Depends(get_db)
) -> list[dict]:
    sc = _get(db, p, scenario_id)
    q = select(Run).where(Run.scenario_id == sc.id).order_by(Run.created_at.desc())
    if p.is_demo:
        q = q.where(Run.is_demo_seed.is_(True), Run.status.in_(["completed", "blocked"]))
    from app.api.runs import run_summary

    return [run_summary(db, r) for r in db.scalars(q)]
