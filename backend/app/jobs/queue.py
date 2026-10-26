"""PostgreSQL-backed job queue with leases.

* Claim with ``FOR UPDATE SKIP LOCKED``; an expired lease is reclaimable, so a
  crashed worker's job is recovered after restart.
* Each claim increments ``attempts``; retries are bounded with backoff.
* Completion is fenced on the lease owner so a stale worker cannot overwrite a
  job another worker has reclaimed.
* Enqueue is idempotent on ``idempotency_key``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import settings
from app.models import JobLease


def enqueue(
    db: Session, kind: str, run_id: uuid.UUID | None, idempotency_key: str, payload: dict[str, Any] | None = None
) -> JobLease:
    stmt = (
        insert(JobLease)
        .values(
            id=uuid.uuid4(),
            run_id=run_id,
            kind=kind,
            idempotency_key=idempotency_key,
            status="pending",
            attempts=0,
            max_attempts=settings.job_max_attempts,
            payload=payload or {},
            available_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
    )
    db.execute(stmt)
    job = db.scalar(select(JobLease).where(JobLease.idempotency_key == idempotency_key))
    assert job is not None
    return job


_CLAIM = text(
    """
    UPDATE job_leases SET status = 'leased', lease_owner = :owner,
           lease_expires_at = now() + make_interval(secs => :lease), attempts = attempts + 1, updated_at = now()
    WHERE id = (
        SELECT id FROM job_leases
        WHERE (status = 'pending' AND available_at <= now())
           OR (status = 'leased' AND lease_expires_at < now())
        ORDER BY created_at
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    RETURNING id
    """
)


def claim(db: Session, owner: str) -> JobLease | None:
    row = db.execute(_CLAIM, {"owner": owner, "lease": settings.lease_seconds}).first()
    db.commit()
    if row is None:
        return None
    return db.get(JobLease, row[0])


def heartbeat(db: Session, job_id: uuid.UUID, owner: str) -> bool:
    res = db.execute(
        text(
            "UPDATE job_leases SET lease_expires_at = now() + make_interval(secs => :lease), updated_at = now() "
            "WHERE id = :id AND lease_owner = :owner AND status = 'leased' RETURNING id"
        ),
        {"id": job_id, "owner": owner, "lease": settings.lease_seconds},
    ).first()
    db.commit()
    return res is not None


def finish(db: Session, job_id: uuid.UUID, owner: str, status: str, error: str | None = None) -> bool:
    res = db.execute(
        text(
            "UPDATE job_leases SET status = :status, last_error = :error, lease_expires_at = NULL, updated_at = now() "
            "WHERE id = :id AND lease_owner = :owner AND status = 'leased' RETURNING id"
        ),
        {"id": job_id, "owner": owner, "status": status, "error": error},
    ).first()
    return res is not None


def release_for_retry(db: Session, job: JobLease, owner: str, error: str) -> bool:
    """Return a job to the queue with backoff, or report False when attempts are exhausted."""
    if job.attempts >= job.max_attempts:
        return False
    delay = min(60, 2**job.attempts)
    db.execute(
        text(
            "UPDATE job_leases SET status = 'pending', lease_owner = NULL, lease_expires_at = NULL, "
            "available_at = now() + make_interval(secs => :delay), last_error = :error, updated_at = now() "
            "WHERE id = :id AND lease_owner = :owner"
        ),
        {"id": job.id, "owner": owner, "delay": delay, "error": error[:2000]},
    )
    return True
