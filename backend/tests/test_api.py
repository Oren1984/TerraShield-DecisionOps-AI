"""API + PostgreSQL integration: auth, permissions, isolation, versioned approval, jobs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

pytestmark = pytest.mark.db

H = {"X-Requested-With": "terrashield"}
OWNER_PW = "harbor-test-passphrase-1"
OPER_PW = "riverside-test-passphrase-1"


def drain(max_jobs: int = 20) -> int:
    from app.worker import process_one

    n = 0
    while n < max_jobs and process_one("test-worker"):
        n += 1
    return n


@pytest.fixture(scope="module")
def app_ready(migrated):
    from app.main import app

    return app


@pytest.fixture(scope="module")
def owner(app_ready):
    c = TestClient(app_ready)
    st = c.get("/api/setup/status").json()
    if st["needs_owner"]:
        weak = c.post("/api/setup/owner", json={"username": "owner", "password": "short"}, headers=H)
        assert weak.status_code == 422
        r = c.post("/api/setup/owner", json={"username": "owner", "password": OWNER_PW}, headers=H)
        assert r.status_code == 201, r.text
    else:
        assert c.post("/api/auth/login", json={"username": "owner", "password": OWNER_PW}, headers=H).status_code == 200
    return c


@pytest.fixture(scope="module")
def workspace(owner):
    return next(w for w in owner.get("/api/workspaces").json() if not w["is_demo"])


def scenario(client, ws, slug):
    return next(s for s in client.get(f"/api/workspaces/{ws['id']}/scenarios").json() if s["slug"] == slug)


def start(client, sc, key=None, **body):
    headers = dict(H, **({"Idempotency-Key": key} if key else {}))
    r = client.post(f"/api/scenarios/{sc['id']}/runs", json={"mode": "mock", **body}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


# ----------------------------------------------------------------------------- auth


def test_setup_is_one_time_and_csrf_required(owner, app_ready):
    assert owner.get("/api/setup/status").json() == {"needs_owner": False}
    c = TestClient(app_ready)
    assert c.post("/api/setup/owner", json={"username": "evil", "password": "x" * 20}, headers=H).status_code == 409
    assert c.post("/api/auth/login", json={"username": "owner", "password": OWNER_PW}).status_code == 403


def test_login_failure_and_lockout(owner, app_ready):
    owner.post(
        "/api/users",
        json={"username": "lockme", "password": "estuary-gate-passphrase-9", "role": "operator"},
        headers=H,
    )
    c = TestClient(app_ready)
    for _ in range(5):
        assert (
            c.post(
                "/api/auth/login", json={"username": "lockme", "password": "wrong-wrong-wrong"}, headers=H
            ).status_code
            == 401
        )
    r = c.post("/api/auth/login", json={"username": "lockme", "password": "estuary-gate-passphrase-9"}, headers=H)
    assert r.status_code == 429
    assert (
        c.post("/api/auth/login", json={"username": "nobody", "password": "whatever-123456"}, headers=H).status_code
        == 401
    )


def test_session_expiry_and_logout(owner, app_ready, db):
    from app.models import AuthSession

    c = TestClient(app_ready)
    assert c.post("/api/auth/login", json={"username": "owner", "password": OWNER_PW}, headers=H).status_code == 200
    assert c.get("/api/auth/me").status_code == 200
    assert c.post("/api/auth/logout", headers=H).status_code == 200
    assert c.get("/api/auth/me").status_code == 401
    c2 = TestClient(app_ready)
    c2.post("/api/auth/login", json={"username": "owner", "password": OWNER_PW}, headers=H)
    db.execute(
        update(AuthSession)
        .where(AuthSession.revoked_at.is_(None), AuthSession.role == "owner")
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    db.commit()
    assert c2.get("/api/auth/me").status_code == 401
    # Re-login the shared owner client after forcing every owner session to expire.
    assert owner.post("/api/auth/login", json={"username": "owner", "password": OWNER_PW}, headers=H).status_code == 200


# ----------------------------------------------------------------------------- roles and isolation


@pytest.fixture(scope="module")
def other_ws(owner):
    r = owner.post("/api/workspaces", json={"name": "Isolated Workspace"}, headers=H)
    assert r.status_code in (201, 409)
    return next(w for w in owner.get("/api/workspaces").json() if w["name"] == "Isolated Workspace")


@pytest.fixture(scope="module")
def operator(owner, workspace, app_ready):
    r = owner.post(
        "/api/users",
        json={"username": "operator1", "password": OPER_PW, "role": "operator", "workspace_ids": [workspace["id"]]},
        headers=H,
    )
    assert r.status_code in (201, 409)
    c = TestClient(app_ready)
    assert c.post("/api/auth/login", json={"username": "operator1", "password": OPER_PW}, headers=H).status_code == 200
    return c


def test_operator_permissions(operator, owner, workspace, other_ws):
    assert operator.get("/api/auth/me").json()["role"] == "operator"
    assert {w["id"] for w in operator.get("/api/workspaces").json()} == {workspace["id"]}
    assert operator.get(f"/api/workspaces/{other_ws['id']}/scenarios").status_code == 404
    assert operator.post("/api/users", json={"username": "xone", "password": "p" * 14}, headers=H).status_code == 403
    assert operator.get("/api/ops/status").status_code == 403
    sc = scenario(operator, workspace, "coastal-storm")
    run = start(operator, sc)
    drain()
    detail = operator.get(f"/api/runs/{run['id']}").json()
    assert detail["status"] == "awaiting_review"
    plan = next(p for p in detail["plans"] if p["rank"] == 1)
    r = operator.post(
        f"/api/runs/{run['id']}/review",
        json={"decision": "approved", "reason": "operator tries", "plan_id": plan["id"]},
        headers=H,
    )
    assert r.status_code == 403
    assert operator.post(f"/api/runs/{run['id']}/simulate", headers=H).status_code == 403
    # Cross-workspace run access is indistinguishable from a missing run.
    other_sc = scenario(owner, other_ws, "coastal-storm")
    other_run = start(owner, other_sc)
    assert operator.get(f"/api/runs/{other_run['id']}").status_code == 404
    assert operator.get(f"/api/runs/{uuid.uuid4()}").status_code == 404


def test_demo_session_is_read_only_and_scoped(app_ready, owner, workspace, db):
    from app.seed import seed_demo

    seed_demo(db)
    db.commit()
    c = TestClient(app_ready)
    assert c.post("/api/auth/demo", headers=H).json()["role"] == "demo"
    wss = c.get("/api/workspaces").json()
    assert len(wss) == 1 and wss[0]["is_demo"]
    runs = c.get(f"/api/workspaces/{wss[0]['id']}/runs").json()
    assert {r["status"] for r in runs} <= {"completed", "blocked"} and len(runs) == 4
    done = next(r for r in runs if r["status"] == "completed")
    assert c.get(f"/api/runs/{done['id']}").status_code == 200
    assert c.get(f"/api/runs/{done['id']}/report").status_code == 200
    # Mutations are refused server-side regardless of the UI.
    sc = c.get(f"/api/workspaces/{wss[0]['id']}/scenarios").json()[0]
    assert c.post(f"/api/scenarios/{sc['id']}/runs", json={"mode": "mock"}, headers=H).status_code == 403
    assert c.post(f"/api/runs/{done['id']}/cancel", headers=H).status_code == 403
    assert c.post("/api/workspaces", json={"name": "demo-made"}, headers=H).status_code == 403
    assert c.get(f"/api/workspaces/{workspace['id']}/runs").status_code == 404
    assert c.get(f"/api/workspaces/{wss[0]['id']}/evidence/search", params={"q": "flood"}).status_code == 403
    assert c.get("/api/models/readiness").json()["local"]["ready"] is None


def test_demo_account_login_is_read_only(app_ready, workspace, db):
    from app.seed import DEMO_PASSWORD, DEMO_USERNAME, seed_demo

    seed_demo(db)
    seed_demo(db)  # idempotent
    db.commit()
    c = TestClient(app_ready)
    me = c.post("/api/auth/login", json={"username": DEMO_USERNAME, "password": DEMO_PASSWORD}, headers=H).json()
    assert me["role"] == "demo" and me["capabilities"] == []
    wss = c.get("/api/workspaces").json()
    assert len(wss) == 1 and wss[0]["is_demo"]
    sc = c.get(f"/api/workspaces/{wss[0]['id']}/scenarios").json()[0]
    assert c.post(f"/api/scenarios/{sc['id']}/runs", json={"mode": "mock"}, headers=H).status_code == 403
    assert c.post("/api/workspaces", json={"name": "demo-made"}, headers=H).status_code == 403
    assert c.get("/api/users").status_code == 403
    assert c.get(f"/api/workspaces/{workspace['id']}/runs").status_code == 404


# ----------------------------------------------------------------------------- full journey and approval binding


def test_full_mock_journey_with_idempotency(owner, workspace):
    sc = scenario(owner, workspace, "cascading-failure")
    key = f"k-{uuid.uuid4()}"
    a = start(owner, sc, key=key)
    b = start(owner, sc, key=key)
    assert a["id"] == b["id"]
    drain()
    d = owner.get(f"/api/runs/{a['id']}").json()
    assert d["status"] == "awaiting_review"
    roles = {x["role"] for x in d["assessments"]}
    assert roles == {"water", "air", "ground", "coordinator"}
    assert all(x["provider"] == "mock" for x in d["assessments"])
    top = next(p for p in d["plans"] if p["rank"] == 1)
    assert all(c["passed"] for c in top["checks"])
    r = owner.post(
        f"/api/runs/{a['id']}/review",
        json={"decision": "approved", "reason": "Protect substation", "plan_id": top["id"]},
        headers=H,
    )
    assert r.json()["status"] == "approved"
    assert owner.post(f"/api/runs/{a['id']}/simulate", headers=H).status_code == 202
    drain()
    d = owner.get(f"/api/runs/{a['id']}").json()
    assert d["status"] == "completed"
    assert d["outcome"]["comparison"]["plan_disruption"] <= d["outcome"]["comparison"]["baseline_disruption"]
    sim = owner.get(f"/api/runs/{a['id']}/simulation").json()
    assert len(sim["hours"]) == d["inputs"]["params"]["duration_h"]
    rep = owner.get(f"/api/runs/{a['id']}/report").json()
    assert rep["decision"]["plan_hash"] == top["plan_hash"] and rep["decision"]["approved_by"] == "owner"
    md = owner.get(f"/api/runs/{a['id']}/report.md")
    assert md.status_code == 200 and "Decision record" in md.text and "not a forecast" in md.text


def test_unverified_plan_cannot_be_approved(owner, workspace):
    sc = scenario(owner, workspace, "wind-saturated-ground")
    run = start(owner, sc)
    drain()
    d = owner.get(f"/api/runs/{run['id']}").json()
    baseline = next(p for p in d["plans"] if p["label"] == "baseline")
    assert baseline["status"] == "rejected_by_verifier"
    r = owner.post(
        f"/api/runs/{run['id']}/review",
        json={"decision": "approved", "reason": "x" * 5, "plan_id": baseline["id"]},
        headers=H,
    )
    assert r.status_code == 409


def test_scenario_edit_invalidates_approval_and_inputs_stay_immutable(owner, workspace):
    sc = scenario(owner, workspace, "coastal-storm")
    run = start(owner, sc)
    drain()
    d = owner.get(f"/api/runs/{run['id']}").json()
    top = next(p for p in d["plans"] if p["rank"] == 1)
    owner.post(
        f"/api/runs/{run['id']}/review",
        json={"decision": "approved", "reason": "go ahead", "plan_id": top["id"]},
        headers=H,
    )
    full = owner.get(f"/api/scenarios/{sc['id']}").json()
    params = full["params"]
    params["rainfall"]["peak_mm_h"] = 40.0
    r = owner.put(
        f"/api/scenarios/{sc['id']}",
        json={"params": params, "change_note": "more rain", "expected_version": full["current_version"]},
        headers=H,
    )
    assert r.status_code == 200
    stale = owner.put(
        f"/api/scenarios/{sc['id']}", json={"params": params, "expected_version": full["current_version"]}, headers=H
    )
    assert stale.status_code == 409
    d2 = owner.get(f"/api/runs/{run['id']}").json()
    assert d2["status"] == "blocked" and "invalidated" in d2["blocked_reason"]
    assert d2["approvals"][-1]["status"] == "invalidated"
    assert d2["inputs"] == d["inputs"] and d2["input_hash"] == d["input_hash"]
    assert owner.post(f"/api/runs/{run['id']}/simulate", headers=H).status_code == 409


def test_approving_after_scenario_change_is_refused(owner, workspace):
    sc = scenario(owner, workspace, "cascading-failure")
    run = start(owner, sc)
    drain()
    full = owner.get(f"/api/scenarios/{sc['id']}").json()
    params = full["params"]
    params["wind"]["peak_ms"] = 11.0
    owner.put(
        f"/api/scenarios/{sc['id']}", json={"params": params, "expected_version": full["current_version"]}, headers=H
    )
    d = owner.get(f"/api/runs/{run['id']}").json()
    assert d["scenario_changed_since"] is True
    top = next(p for p in d["plans"] if p["rank"] == 1)
    r = owner.post(
        f"/api/runs/{run['id']}/review",
        json={"decision": "approved", "reason": "go ahead", "plan_id": top["id"]},
        headers=H,
    )
    assert r.status_code == 409


def test_reject_and_revision_flows(owner, workspace):
    sc = scenario(owner, workspace, "wind-saturated-ground")
    run = start(owner, sc)
    drain()
    d = owner.get(f"/api/runs/{run['id']}").json()
    top = next(p for p in d["plans"] if p["rank"] == 1)
    excluded = [f"{a['type']}:{a['target']}" for a in top["actions"] if a["type"] != "mobile_comms_unit"][:1]
    r = owner.post(
        f"/api/runs/{run['id']}/review",
        json={
            "decision": "revision_requested",
            "reason": "avoid slope work",
            "excluded_actions": excluded,
            "budget_cap": 9000,
        },
        headers=H,
    )
    assert r.json()["status"] == "queued" and r.json()["revision"] == 1
    drain()
    d = owner.get(f"/api/runs/{run['id']}").json()
    assert d["status"] == "awaiting_review"
    rev1 = [p for p in d["plans"] if p["revision"] == 1]
    assert rev1 and all(f"{a['type']}:{a['target']}" not in excluded for p in rev1 for a in p["actions"])
    assert all(p["objective"]["cost"] <= 9000 for p in rev1 if p["status"] == "verified")
    r = owner.post(f"/api/runs/{run['id']}/review", json={"decision": "rejected", "reason": "not needed"}, headers=H)
    assert r.json()["status"] == "rejected"
    assert (
        owner.post(
            f"/api/runs/{run['id']}/review", json={"decision": "rejected", "reason": "again"}, headers=H
        ).status_code
        == 409
    )


def test_blocked_conflict_scenario_and_prompt_injection_notes(owner, workspace):
    sc = scenario(owner, workspace, "conflict-insufficient-resources")
    full = owner.get(f"/api/scenarios/{sc['id']}").json()
    assert "ignore all previous instructions" in full["params"]["notes"].lower()
    run = start(owner, sc)
    drain()
    d = owner.get(f"/api/runs/{run['id']}").json()
    assert d["status"] == "blocked" and "A2" in d["blocked_reason"]
    assert d["approvals"] == []
    kinds = {e["kind"] for e in owner.get(f"/api/runs/{run['id']}/events").json()["events"]}
    assert "conflict_detected" in kinds
    assert d["evidence"]["explanation"]["recommended_plan"] == "none"


def test_local_mode_refused_when_model_not_ready(owner, workspace, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ollama_base_url", "http://127.0.0.1:9")
    sc = scenario(owner, workspace, "coastal-storm")
    r = owner.post(f"/api/scenarios/{sc['id']}/runs", json={"mode": "local"}, headers=H)
    assert r.status_code == 409 and "Mock" in r.json()["detail"]


# ----------------------------------------------------------------------------- cancellation, crash recovery, retries


def test_cancel_queued_run(owner, workspace, db):
    from app.models import JobLease

    sc = scenario(owner, workspace, "coastal-storm")
    run = start(owner, sc)
    r = owner.post(f"/api/runs/{run['id']}/cancel", headers=H).json()
    assert r["status"] == "cancelled" and r["cancel_result"] == "cancelled"
    job = db.scalar(select(JobLease).where(JobLease.run_id == uuid.UUID(run["id"])))
    assert job.status == "cancelled"
    assert drain() == 0


def test_cancel_race_with_running_worker(owner, workspace, db):
    from app.jobs.statemachine import transition

    sc = scenario(owner, workspace, "coastal-storm")
    run = start(owner, sc)
    rid = uuid.UUID(run["id"])
    assert transition(db, rid, {"queued"}, "assessing", "test", "worker picked it up")
    db.commit()
    r = owner.post(f"/api/runs/{run['id']}/cancel", headers=H).json()
    assert r["cancel_result"] == "cancel_requested"
    drain()
    d = owner.get(f"/api/runs/{run['id']}").json()
    assert d["status"] == "cancelled"
    assert d["plans"] == []


def test_worker_crash_recovery_and_lease_fencing(owner, workspace, db):
    from app.jobs import queue
    from app.models import JobLease, Run

    sc = scenario(owner, workspace, "coastal-storm")
    run = start(owner, sc)
    rid = uuid.UUID(run["id"])
    job = queue.claim(db, "crashed-worker")
    assert job is not None and job.run_id == rid and job.attempts == 1
    # Worker dies: the lease expires without completion.
    db.execute(
        update(JobLease).where(JobLease.id == job.id).values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=5))
    )
    db.commit()
    assert drain() >= 1
    db.expire_all()
    job2 = db.get(JobLease, job.id)
    assert job2.status == "done" and job2.attempts == 2 and job2.lease_owner == "test-worker"
    assert db.get(Run, rid).status == "awaiting_review"
    # The crashed worker can no longer complete the reclaimed job.
    assert queue.finish(db, job.id, "crashed-worker", "failed", "late") is False


def test_retries_exhausted_fail_the_run(owner, workspace, db):
    from app.models import JobLease, Run

    sc = scenario(owner, workspace, "coastal-storm")
    run = start(owner, sc)
    rid = uuid.UUID(run["id"])
    db.execute(update(JobLease).where(JobLease.run_id == rid).values(attempts=3, max_attempts=3))
    db.commit()
    drain()
    db.expire_all()
    r = db.get(Run, rid)
    assert r.status == "failed" and r.failure_code == "retries_exhausted"
    resp = owner.post(f"/api/runs/{run['id']}/retry", json={"mode": "mock"}, headers=H)
    assert resp.status_code == 201 and resp.json()["retry_of"] == run["id"]


def test_model_failure_is_recorded_not_relabelled(owner, workspace, db, monkeypatch):
    from app.agents import workflow
    from app.agents.providers import ProviderError
    from app.models import Run, SpecialistAssessment

    class Failing:
        name = "ollama"
        model_id = "ollama:test-model"

        def generate(self, *a, **k):
            raise ProviderError("model_timeout", "no response within 1s")

    monkeypatch.setattr(workflow, "make_provider", lambda mode, seed: Failing())
    sc = scenario(owner, workspace, "coastal-storm")
    run = start(owner, sc)
    drain()
    db.expire_all()
    r = db.get(Run, uuid.UUID(run["id"]))
    assert r.status == "failed" and r.failure_code == "model_timeout"
    rows = list(db.scalars(select(SpecialistAssessment).where(SpecialistAssessment.run_id == r.id)))
    assert rows and all(x.provider == "ollama" and x.status == "invalid" for x in rows)


def test_injection_obeying_model_is_rejected_and_revisions_bounded(owner, workspace, db, monkeypatch):
    from app.agents import workflow
    from app.agents.providers import MockProvider
    from app.config import settings
    from app.models import Run, RunEvent

    class Obedient(MockProvider):
        """Follows the injected note: tries to add an approval flag."""

        def generate(self, role, step, payload, schema, errors=None):
            g = super().generate(role, step, payload, schema, errors)
            if role == "water":
                g.raw = dict(g.raw, approved=True)
            return g

    monkeypatch.setattr(workflow, "make_provider", lambda mode, seed: Obedient(seed))
    sc = scenario(owner, workspace, "conflict-insufficient-resources")
    run = start(owner, sc)
    drain()
    db.expire_all()
    r = db.get(Run, uuid.UUID(run["id"]))
    assert r.status == "failed" and r.failure_code == "agent_output_invalid"

    class Insistent(MockProvider):
        """Coordinator always asks for another revision."""

        def _coordinator_consolidate(self, p):
            out = super()._coordinator_consolidate(dict(p, allow_revisions=True))
            if p["conflicts"]:
                c = p["conflicts"][0]
                out["revision_requests"] = [
                    {"role": c["proposer"] if "proposer" in c else "water", "conflict_id": c["id"], "request": "again"}
                ]
            return out

    monkeypatch.setattr(settings, "max_revision_rounds", 1)
    monkeypatch.setattr(workflow, "make_provider", lambda mode, seed: Insistent(seed))
    sc2 = scenario(owner, workspace, "wind-saturated-ground")
    run2 = start(owner, sc2)
    drain()
    rid2 = uuid.UUID(run2["id"])
    kinds = [e.kind for e in db.scalars(select(RunEvent).where(RunEvent.run_id == rid2).order_by(RunEvent.id))]
    assert kinds.count("revision_requested") <= 1
    assert db.get(Run, rid2).status in ("awaiting_review", "blocked")


# ----------------------------------------------------------------------------- evidence retrieval isolation


def test_prior_report_retrieval_is_workspace_scoped(owner, workspace, other_ws, db):
    from app.agents.retrieval import FIXTURE_MODEL_ID, retrieve
    from app.models import Run

    done = db.scalar(select(Run).where(Run.workspace_id == uuid.UUID(workspace["id"]), Run.status == "completed"))
    assert done is not None
    assert owner.post(f"/api/runs/{done.id}/library", headers=H).status_code == 201
    q = "Prior run summary decision record approved plan"
    mine = retrieve(db, uuid.UUID(workspace["id"]), "coordinator", q, FIXTURE_MODEL_ID, k=10)
    theirs = retrieve(db, uuid.UUID(other_ws["id"]), "coordinator", q, FIXTURE_MODEL_ID, k=10)
    assert any(c.document_id == f"prior-report-{done.id}" for c in mine.chunks)
    assert not any(c.document_id.startswith("prior-report-") for c in theirs.chunks)
    stale = retrieve(
        db,
        uuid.UUID(workspace["id"]),
        "ground",
        "restricted only above 0.6 lifted when rainfall stops",
        FIXTURE_MODEL_ID,
        k=10,
    )
    assert not any(c.document_id == "guide-ground-slope-restrictions-v1" for c in stale.chunks)
    # Re-seeding (every migrate run) must cope with library documents absent from the fixture file.
    from app.seed import seed_corpus

    seed_corpus(db)
    db.commit()
    wrong_role = retrieve(
        db, uuid.UUID(workspace["id"]), "air", "slope susceptibility restriction levels", FIXTURE_MODEL_ID, k=10
    )
    assert not any(c.document_id == "guide-ground-slope-restrictions" for c in wrong_role.chunks)


def test_health_and_ops(owner, app_ready):
    c = TestClient(app_ready)
    assert c.get("/health/live").json() == {"status": "ok"}
    ready = c.get("/health/ready")
    assert ready.status_code == 200 and ready.json()["checks"]["migrations"] == "ok"
    ops = owner.get("/api/ops/status").json()
    assert "jobs" in ops and "analysis_latency" in ops and ops["job_retries"] >= 1


def test_reseeding_keeps_other_model_embeddings(db):
    from app.models import Chunk, ChunkEmbedding, EmbeddingModel
    from app.seed import seed_corpus

    if db.get(EmbeddingModel, "test:tiny") is None:
        db.add(
            EmbeddingModel(id="test:tiny", provider="ollama", name="tiny", dimension=3, version="1", is_semantic=True)
        )
        db.flush()
    chunk = db.scalar(select(Chunk).limit(1))
    db.merge(ChunkEmbedding(chunk_id=chunk.id, model_id="test:tiny", embedding=[0.1, 0.2, 0.3]))
    db.commit()
    seed_corpus(db)
    db.commit()
    assert db.get(ChunkEmbedding, (chunk.id, "test:tiny")) is not None
