"""Local evidence retrieval with pgvector.

* Workspace and role filters are applied in SQL *before* similarity ranking;
  other workspaces' documents are never candidates.
* Superseded documents are excluded.
* Exact (sequential) cosine search; no ANN index for this small corpus.
* Two embedders: a labelled lexical *fixture* (feature hashing, not semantic)
  for Mock mode, and a real local Ollama embedding model for Local mode.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Chunk, ChunkEmbedding, Document, EmbeddingModel

FIXTURE_MODEL_ID = "fixture-hash-256-v1"
FIXTURE_DIM = 256
_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = set("a an the of to in on for and or is are be by with at as it its this that from into than not no".split())

# Minimum cosine similarity per embedding model for a chunk to count as relevant.
# Fixture value chosen by inspection of the lexical fixture; Ollama value measured in
# the retrieval evaluation (see docs/GOVERNANCE_AND_VALIDATION.md).
MIN_SIMILARITY = {FIXTURE_MODEL_ID: 0.15}
DEFAULT_MIN_SIMILARITY = 0.55


def ollama_model_id(name: str | None = None) -> str:
    return f"ollama:{name or settings.ollama_embed_model}"


def fixture_embed(text: str) -> list[float]:
    """Deterministic feature-hashing vector of unigrams and bigrams (lexical, not semantic)."""
    toks = [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]
    feats = toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:], strict=False)]
    vec = [0.0] * FIXTURE_DIM
    for f in feats:
        h = hashlib.sha256(f.encode()).digest()
        idx = int.from_bytes(h[:4], "big") % FIXTURE_DIM
        vec[idx] += 1.0 if h[4] & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]


def ollama_embed(texts: list[str], model: str | None = None, timeout: float = 120.0) -> list[list[float]]:
    r = httpx.post(
        f"{settings.ollama_base_url}/api/embed",
        json={"model": model or settings.ollama_embed_model, "input": texts},
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()["embeddings"]


@dataclass
class Retrieved:
    chunk_id: str
    document_id: str
    title: str
    heading: str
    text: str
    similarity: float
    document_version: str
    provenance: str
    kind: str

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class RetrievalResult:
    query: str
    model_id: str
    status: str  # ok | insufficient_evidence | embeddings_missing | embedder_unavailable
    chunks: list[Retrieved] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "model_id": self.model_id,
            "status": self.status,
            "chunks": [c.as_dict() for c in self.chunks],
        }


def embedding_status(db: Session, model_id: str) -> dict[str, Any]:
    total = db.scalar(select(func.count()).select_from(Chunk).join(Document).where(Document.status == "active")) or 0
    done = db.scalar(select(func.count()).select_from(ChunkEmbedding).where(ChunkEmbedding.model_id == model_id)) or 0
    model = db.get(EmbeddingModel, model_id)
    return {
        "model_id": model_id,
        "registered": model is not None,
        "dimension": model.dimension if model else None,
        "embedded_chunks": done,
        "active_chunks": total,
        "complete": model is not None and done >= total and total > 0,
    }


def retrieve(
    db: Session,
    workspace_id: uuid.UUID,
    role: str,
    query: str,
    model_id: str,
    query_vector: list[float] | None = None,
    k: int | None = None,
) -> RetrievalResult:
    k = k or settings.retrieval_top_k
    model = db.get(EmbeddingModel, model_id)
    if model is None:
        return RetrievalResult(query, model_id, "embeddings_missing")
    if query_vector is None:
        if model.provider == "fixture":
            query_vector = fixture_embed(query)
        else:
            try:
                query_vector = ollama_embed([query], model.name)[0]
            except (httpx.HTTPError, KeyError, IndexError):
                return RetrievalResult(query, model_id, "embedder_unavailable")
    if len(query_vector) != model.dimension:
        return RetrievalResult(query, model_id, "embeddings_missing")
    dist = ChunkEmbedding.embedding.cosine_distance(query_vector)
    rows = db.execute(
        select(Chunk, Document, (1 - dist).label("sim"))
        .join(Document, Chunk.document_id == Document.id)
        .join(ChunkEmbedding, ChunkEmbedding.chunk_id == Chunk.id)
        .where(
            ChunkEmbedding.model_id == model_id,
            Document.status == "active",
            (Document.workspace_id.is_(None)) | (Document.workspace_id == workspace_id),
            Document.roles.contains([role]),
        )
        .order_by(dist, Chunk.id)
        .limit(k)
    ).all()
    threshold = MIN_SIMILARITY.get(model_id, DEFAULT_MIN_SIMILARITY)
    chunks = [
        Retrieved(
            chunk_id=c.id,
            document_id=d.id,
            title=d.title,
            heading=c.heading,
            text=c.text,
            similarity=round(float(sim), 4),
            document_version=c.document_version,
            provenance=d.provenance,
            kind=d.kind,
        )
        for c, d, sim in rows
        if float(sim) >= threshold
    ]
    return RetrievalResult(query, model_id, "ok" if chunks else "insufficient_evidence", chunks)


def citation_permitted(db: Session, workspace_id: uuid.UUID, role: str, chunk_id: str) -> bool:
    row = db.execute(
        select(Chunk.id)
        .join(Document)
        .where(
            Chunk.id == chunk_id,
            Document.status == "active",
            (Document.workspace_id.is_(None)) | (Document.workspace_id == workspace_id),
            Document.roles.contains([role]),
        )
    ).first()
    return row is not None


# --- Corpus ingestion -------------------------------------------------------------


def parse_markdown_doc(text: str) -> tuple[dict[str, str], list[tuple[str, str]]]:
    meta: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        _, fm, body = text.split("---", 2)
        for line in fm.strip().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
    sections: list[tuple[str, str]] = []
    heading = meta.get("title", "")
    buf: list[str] = []
    for line in body.strip().splitlines():
        if line.startswith("## "):
            if buf:
                sections.append((heading, " ".join(buf).strip()))
            heading, buf = line[3:].strip(), []
        elif line.strip():
            buf.append(line.strip())
    if buf:
        sections.append((heading, " ".join(buf).strip()))
    return meta, sections


def load_corpus(corpus_dir: Path) -> list[dict[str, Any]]:
    docs = []
    for path in sorted(corpus_dir.glob("*.md")):
        meta, sections = parse_markdown_doc(path.read_text(encoding="utf-8"))
        docs.append({"meta": meta, "sections": sections})
    return docs


def ingest_document(
    db: Session,
    doc_id: str,
    title: str,
    kind: str,
    roles: list[str],
    version: str,
    status: str,
    provenance: str,
    license_: str,
    updated: str,
    sections: list[tuple[str, str]],
    workspace_id: uuid.UUID | None = None,
    superseded_by: str | None = None,
) -> list[Chunk]:
    doc = db.get(Document, doc_id)
    if doc is None:
        doc = Document(id=doc_id)
        db.add(doc)
    doc.workspace_id = workspace_id
    doc.title, doc.kind, doc.roles, doc.version = title, kind, roles, version
    doc.status, doc.superseded_by, doc.provenance = status, superseded_by, provenance
    doc.license, doc.source_updated_at = license_, updated
    db.flush()
    existing = list(db.scalars(select(Chunk).where(Chunk.document_id == doc_id).order_by(Chunk.ordinal)))
    wanted = [(f"{doc_id}#{i + 1}@{version}", h, t) for i, (h, t) in enumerate(sections)]
    if [(c.id, c.heading, c.text) for c in existing] == wanted:
        # Unchanged content: keep chunks and every model's embeddings (re-seeding must not wipe them).
        return existing
    for old in existing:
        db.delete(old)
    db.flush()
    chunks = []
    for i, (heading, text) in enumerate(sections):
        c = Chunk(
            id=f"{doc_id}#{i + 1}@{version}",
            document_id=doc_id,
            ordinal=i + 1,
            heading=heading,
            text=text,
            document_version=version,
        )
        db.add(c)
        chunks.append(c)
    db.flush()
    return chunks


def ensure_fixture_model(db: Session) -> None:
    if db.get(EmbeddingModel, FIXTURE_MODEL_ID) is None:
        db.add(
            EmbeddingModel(
                id=FIXTURE_MODEL_ID,
                provider="fixture",
                name="feature-hash",
                dimension=FIXTURE_DIM,
                version="1",
                is_semantic=False,
            )
        )
        db.flush()


def embed_chunks(db: Session, model_id: str, vectors: dict[str, list[float]] | None = None) -> int:
    """(Re-)embed all active chunks for one model. Explicit, versioned job."""
    model = db.get(EmbeddingModel, model_id)
    if model is None:
        raise ValueError(f"embedding model {model_id} is not registered")
    chunks = list(db.scalars(select(Chunk).join(Document).where(Document.status == "active")))
    texts = [f"{c.heading}. {c.text}" for c in chunks]
    if vectors is None:
        if model.provider == "fixture":
            vecs = [fixture_embed(t) for t in texts]
        else:
            vecs = []
            for i in range(0, len(texts), 16):
                vecs += ollama_embed(texts[i : i + 16], model.name)
    else:
        vecs = [vectors[c.id] for c in chunks]
    for c, v in zip(chunks, vecs, strict=True):
        if len(v) != model.dimension:
            raise ValueError(f"dimension mismatch for {c.id}: {len(v)} != {model.dimension}")
        row = db.get(ChunkEmbedding, (c.id, model_id))
        if row is None:
            db.add(ChunkEmbedding(chunk_id=c.id, model_id=model_id, embedding=v))
        else:
            row.embedding = v
    db.flush()
    return len(chunks)


def register_ollama_model(db: Session, name: str | None = None) -> EmbeddingModel:
    name = name or settings.ollama_embed_model
    model_id = ollama_model_id(name)
    probe = ollama_embed(["dimension probe"], name)[0]
    digest = "unknown"
    try:
        tags = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=10).json()
        for m in tags.get("models", []):
            if m.get("name", "").split(":")[0] == name.split(":")[0]:
                digest = m.get("digest", "unknown")[:12]
    except (httpx.HTTPError, json.JSONDecodeError):
        pass
    row = db.get(EmbeddingModel, model_id)
    if row is None:
        row = EmbeddingModel(
            id=model_id, provider="ollama", name=name, dimension=len(probe), version=digest, is_semantic=True
        )
        db.add(row)
    elif row.dimension != len(probe) or row.version != digest:
        # Model changed: drop stale vectors; re-embedding is an explicit step.
        for e in db.scalars(select(ChunkEmbedding).where(ChunkEmbedding.model_id == model_id)):
            db.delete(e)
        row.dimension, row.version = len(probe), digest
    db.flush()
    return row
