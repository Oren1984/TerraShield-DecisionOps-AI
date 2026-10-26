"""Real local-model checks. Opt-in: needs a running Ollama with the configured models.

Run with ``pytest -m ollama`` (and TS_TEST_DATABASE_URL for the retrieval test).
These are the only tests that count as local-inference validation; Mock tests never do.
"""

from __future__ import annotations

import os
import uuid

import pytest

from app.agents.providers import OllamaProvider, readiness
from app.agents.schemas import SpecialistAssessment

pytestmark = pytest.mark.ollama

if os.environ.get("TS_RUN_OLLAMA_TESTS") != "1":
    pytest.skip("set TS_RUN_OLLAMA_TESTS=1 to run real Ollama tests", allow_module_level=True)


def test_ollama_ready():
    r = readiness()
    assert r["reachable"] and r["chat_model_present"] and r["embed_model_present"], r


def test_real_embedding_pipeline(db):
    from app.agents.retrieval import embed_chunks, embedding_status, register_ollama_model, retrieve

    model = register_ollama_model(db)
    n = embed_chunks(db, model.id)
    db.commit()
    assert n > 0 and model.dimension > 0
    status = embedding_status(db, model.id)
    assert status["complete"]
    from sqlalchemy import select

    from app.models import Workspace

    ws = db.scalar(select(Workspace).where(Workspace.is_demo.is_(False))) or db.scalar(select(Workspace))
    wid = ws.id if ws else uuid.uuid4()
    res = retrieve(db, wid, "ground", "When must a slope road be restricted?", model.id)
    assert res.status == "ok"
    assert res.chunks[0].document_id == "guide-ground-slope-restrictions"


def test_real_specialist_call_validates_or_fails_cleanly(district, coastal):
    from app.agents.evidence import build_evidence, evidence_for_role
    from app.agents.schemas import ROLE_ACTIONS
    from app.agents.validate import ValidationContext, validate_specialist
    from app.simulation.actions import valid_targets
    from app.simulation.engine import simulate

    sim = simulate(district, coastal)
    ev = build_evidence(district, coastal, sim)
    ctx = ValidationContext(district, coastal, ev)
    payload = {
        "role": "water",
        "evidence": evidence_for_role(ev, "water"),
        "allowed_actions": [
            {"type": t, "valid_targets": valid_targets(district, t)} for t in sorted(ROLE_ACTIONS["water"])
        ],
        "resources": coastal.resources.model_dump(),
        "guidance": [],
        "scenario_notes_untrusted": coastal.notes,
    }
    gen = OllamaProvider(seed=1).generate("water", "assess", payload, SpecialistAssessment)
    out, errors = validate_specialist(gen.raw, "water", ctx)
    # Either a valid output or explicit validator errors; never an exception or silent acceptance.
    assert (out is not None) != bool(errors)
    print(f"latency_ms={gen.latency_ms} output_tokens={gen.output_tokens} valid={out is not None} errors={errors[:3]}")
