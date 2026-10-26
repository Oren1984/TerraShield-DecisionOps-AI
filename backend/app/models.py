"""Relational model. Relational and vector data share one PostgreSQL database."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> uuid.UUID:
    return uuid.uuid4()


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16))  # owner | operator
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16))  # owner | operator | demo
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True)


class DistrictAsset(Base):
    __tablename__ = "assets"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    district_version: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(32))
    zone: Mapped[str] = mapped_column(String(8))
    properties: Mapped[dict[str, Any]] = mapped_column(JSONB)


class RoadEdgeRow(Base):
    __tablename__ = "road_edges"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    district_version: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    from_junction: Mapped[str] = mapped_column(String(16))
    to_junction: Mapped[str] = mapped_column(String(16))
    properties: Mapped[dict[str, Any]] = mapped_column(JSONB)


class Scenario(Base):
    __tablename__ = "scenarios"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(160))
    summary: Mapped[str] = mapped_column(Text, default="")
    template: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # provenance, assumptions, expected behavior
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("workspace_id", "slug"),)


class ScenarioVersion(Base):
    __tablename__ = "scenario_versions"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    scenario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    seed: Mapped[int] = mapped_column(BigInteger)
    input_hash: Mapped[str] = mapped_column(String(64))
    change_note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("scenario_id", "version"),)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    scenario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    scenario_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scenario_versions.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(24), index=True)
    mode: Mapped[str] = mapped_column(String(8))  # mock | local
    seed: Mapped[int] = mapped_column(BigInteger)
    simulator_version: Mapped[str] = mapped_column(String(32))
    planner_version: Mapped[str] = mapped_column(String(32))
    policy_version: Mapped[str] = mapped_column(String(32))
    workflow_version: Mapped[str] = mapped_column(String(32))
    model_id: Mapped[str | None] = mapped_column(String(120))
    embedding_model_id: Mapped[str | None] = mapped_column(String(120))
    input_hash: Mapped[str] = mapped_column(String(64))
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB)  # immutable copy of params used
    revision: Mapped[int] = mapped_column(Integer, default=0)
    revision_constraints: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    baseline: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    outcome: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_detail: Mapped[str | None] = mapped_column(Text)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    is_demo_seed: Mapped[bool] = mapped_column(Boolean, default=False)
    retry_of: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    analysis_seconds: Mapped[float | None] = mapped_column(Float)


class RunEvent(Base):
    """Execution trace: actual workflow events, never private model reasoning."""

    __tablename__ = "run_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor: Mapped[str] = mapped_column(String(24))
    kind: Mapped[str] = mapped_column(String(48))
    message: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class JobLease(Base):
    __tablename__ = "job_leases"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24))  # analyze | replan | simulate | embed
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    status: Mapped[str] = mapped_column(String(16), index=True)  # pending | leased | done | failed | cancelled
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    lease_owner: Mapped[str | None] = mapped_column(String(80))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SpecialistAssessment(Base):
    __tablename__ = "specialist_assessments"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    round: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))  # water | air | ground | coordinator
    step: Mapped[str] = mapped_column(String(24))  # assess | revise | consolidate | explain
    provider: Mapped[str] = mapped_column(String(16))
    model_id: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16))  # valid | invalid
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    validation_errors: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    retrieved_chunk_ids: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CandidatePlanRow(Base):
    __tablename__ = "candidate_plans"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    label: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(120))
    method: Mapped[str] = mapped_column(String(24))
    solver_status: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24))  # verified | rejected_by_verifier | infeasible
    rank: Mapped[int | None] = mapped_column(Integer)
    actions: Mapped[list[Any]] = mapped_column(JSONB)
    objective: Mapped[dict[str, Any]] = mapped_column(JSONB)
    plan_hash: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[dict[str, Any]] = mapped_column(JSONB)  # simulated summary + per-hour disruption
    sensitivity: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    notes: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ConstraintResult(Base):
    __tablename__ = "constraint_results"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidate_plans.id", ondelete="CASCADE"), index=True)
    constraint: Mapped[str] = mapped_column(String(40))
    passed: Mapped[bool] = mapped_column(Boolean)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    plan_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("candidate_plans.id", ondelete="CASCADE"))
    scenario_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scenario_versions.id", ondelete="RESTRICT"))
    plan_hash: Mapped[str | None] = mapped_column(String(64))
    input_hash: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(24))  # approved | rejected | revision_requested
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))  # active | invalidated | consumed | closed
    invalidated_reason: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    decider_label: Mapped[str] = mapped_column(String(120))  # username, or "seed fixture" for demo data
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SimulatedEvent(Base):
    __tablename__ = "simulated_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidate_plans.id", ondelete="CASCADE"))
    hour: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("run_id", "plan_id", "hour", "kind"),)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), unique=True)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    markdown: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    """Application audit record. Append-only by convention; not tamper-proof."""

    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_role: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64))
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workspaces.id", ondelete="SET NULL"))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(120))
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EmbeddingModel(Base):
    __tablename__ = "embedding_models"
    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    provider: Mapped[str] = mapped_column(String(24))  # fixture | ollama
    name: Mapped[str] = mapped_column(String(120))
    dimension: Mapped[int] = mapped_column(Integer)
    version: Mapped[str] = mapped_column(String(64))
    is_semantic: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(32))  # guidance | assumption | reference | prior_report
    roles: Mapped[list[str]] = mapped_column(ARRAY(String(16)))
    version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))  # active | superseded
    superseded_by: Mapped[str | None] = mapped_column(String(80))
    provenance: Mapped[str] = mapped_column(Text)
    license: Mapped[str] = mapped_column(String(120))
    source_updated_at: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Chunk(Base):
    __tablename__ = "chunks"
    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    document_version: Mapped[str] = mapped_column(String(32))


class ChunkEmbedding(Base):
    __tablename__ = "chunk_embeddings"
    chunk_id: Mapped[str] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"), primary_key=True)
    model_id: Mapped[str] = mapped_column(ForeignKey("embedding_models.id", ondelete="CASCADE"), primary_key=True)
    embedding: Mapped[Any] = mapped_column(Vector())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
