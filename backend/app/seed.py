"""Reference data and demo seeding. Idempotent; run by the one-shot migrate job.

Demo runs are produced by the real workflow in Mock mode. Their approvals are
labelled "seed fixture" because no person made them.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.retrieval import (
    FIXTURE_DIM,
    FIXTURE_MODEL_ID,
    embed_chunks,
    ensure_fixture_model,
    fixture_embed,
    ingest_document,
    load_corpus,
)
from app.agents.workflow import Workflow
from app.config import settings
from app.jobs.runner import simulate_approved
from app.jobs.statemachine import transition
from app.models import Approval, CandidatePlanRow, DistrictAsset, JobLease, RoadEdgeRow, Run, Scenario, Workspace
from app.services import seed_workspace_scenarios
from app.simulation.district import load_district

logger = logging.getLogger("terrashield.seed")

DEMO_WORKSPACE = "Demo: Harbor Vale (read-only)"
FIXTURE_VECTORS = "fixture_vectors.json"


def corpus_dir() -> Path:
    return Path(settings.scenario_dir) / "corpus"


def seed_district(db: Session) -> None:
    d = load_district()
    for a in d.raw["assets"]:
        row = db.get(DistrictAsset, (a["id"], d.version)) or DistrictAsset(id=a["id"], district_version=d.version)
        row.name, row.type, row.zone, row.properties = a["name"], a["type"], a["zone"], a
        db.merge(row)
    for e in d.raw["road_edges"]:
        edge = db.get(RoadEdgeRow, (e["id"], d.version)) or RoadEdgeRow(id=e["id"], district_version=d.version)
        edge.name, edge.from_junction, edge.to_junction, edge.properties = e["name"], e["from"], e["to"], e
        db.merge(edge)
    db.flush()


def seed_corpus(db: Session) -> int:
    count = 0
    for doc in load_corpus(corpus_dir()):
        m = doc["meta"]
        ingest_document(
            db,
            m["id"],
            m["title"],
            m["kind"],
            [r.strip() for r in m["roles"].split(",")],
            m["version"],
            m["status"],
            m["provenance"],
            m["license"],
            m["updated"],
            doc["sections"],
            superseded_by=m.get("superseded_by"),
        )
        count += 1
    ensure_fixture_model(db)
    path = corpus_dir() / FIXTURE_VECTORS
    vectors = json.loads(path.read_text(encoding="utf-8"))["vectors"]
    embed_chunks(db, FIXTURE_MODEL_ID, vectors)
    return count


def build_fixture_vectors() -> dict:
    """Precompute the labelled lexical fixture vectors shipped with the repository."""
    out: dict[str, list[float]] = {}
    for doc in load_corpus(corpus_dir()):
        m = doc["meta"]
        if m["status"] != "active":
            continue
        for i, (heading, text) in enumerate(doc["sections"]):
            out[f"{m['id']}#{i + 1}@{m['version']}"] = fixture_embed(f"{heading}. {text}")
    return {
        "model_id": FIXTURE_MODEL_ID,
        "dimension": FIXTURE_DIM,
        "label": "Lexical feature-hashing fixture for Mock mode. Not a semantic embedding model.",
        "vectors": out,
    }


def seed_demo(db: Session) -> None:
    ws = db.scalar(select(Workspace).where(Workspace.name == DEMO_WORKSPACE))
    if ws is None:
        ws = Workspace(name=DEMO_WORKSPACE, is_demo=True)
        db.add(ws)
        db.flush()
    seed_workspace_scenarios(db, ws.id, None)
    db.commit()
    from app.services import create_run

    for sc in db.scalars(select(Scenario).where(Scenario.workspace_id == ws.id).order_by(Scenario.slug)):
        done = db.scalar(
            select(Run).where(
                Run.scenario_id == sc.id, Run.is_demo_seed.is_(True), Run.status.in_(["completed", "blocked"])
            )
        )
        if done:
            continue
        run = create_run(db, None, sc, "mock", is_demo_seed=True)
        # Execute inline instead of via the worker so the demo exists right after migration.
        for job in db.scalars(select(JobLease).where(JobLease.run_id == run.id)):
            job.status = "done"
        db.commit()
        Workflow(db, run).analyze()
        db.refresh(run)
        if run.status == "awaiting_review":
            top = db.scalar(
                select(CandidatePlanRow).where(CandidatePlanRow.run_id == run.id, CandidatePlanRow.rank == 1)
            )
            assert top is not None
            db.add(
                Approval(
                    run_id=run.id,
                    plan_id=top.id,
                    scenario_version_id=run.scenario_version_id,
                    plan_hash=top.plan_hash,
                    input_hash=run.input_hash,
                    decision="approved",
                    status="active",
                    decided_by=None,
                    decider_label="seed fixture (automated demo data)",
                    reason="Demo data: top-ranked verified plan approved automatically during seeding.",
                )
            )
            transition(
                db, run.id, {"awaiting_review"}, "approved", "seed", "Seed fixture approved the top-ranked plan."
            )
            db.commit()
            db.refresh(run)
            delay = settings.sim_step_delay_ms
            settings.sim_step_delay_ms = 0
            try:
                simulate_approved(db, run, lambda: None)
            finally:
                settings.sim_step_delay_ms = delay
        db.commit()
        logger.info("seeded demo run %s for %s: %s", run.id, sc.slug, run.status)


def seed_all(db: Session, demo: bool = True) -> None:
    seed_district(db)
    seed_corpus(db)
    db.commit()
    if demo:
        seed_demo(db)
    db.commit()
