"""Background worker: claims leased jobs and executes them.

Run with ``python -m app.worker``. Multiple workers may run; leases keep each
job with one owner and expired leases are reclaimed after a crash.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
import uuid

from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal
from app.jobs import queue
from app.jobs.runner import TerminalJobError, execute
from app.jobs.statemachine import transition
from app.logging_setup import configure_logging, log
from app.models import JobLease

logger = logging.getLogger("terrashield.worker")
_stop = threading.Event()


class LeaseLost(Exception):
    pass


def _owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


def process_one(owner: str) -> bool:
    """Claim and run one job. Returns True if a job was processed."""
    db = SessionLocal()
    try:
        job = queue.claim(db, owner)
        if job is None:
            return False
        log(logger, logging.INFO, "job claimed", job_id=str(job.id), kind=job.kind, attempt=job.attempts)
        if job.attempts > job.max_attempts:
            _fail(db, job, owner, "retries_exhausted", job.last_error or "attempt limit reached")
            return True

        hb_db = SessionLocal()
        last = [time.monotonic()]

        def checkpoint() -> None:
            if time.monotonic() - last[0] >= settings.lease_seconds / 3:
                if not queue.heartbeat(hb_db, job.id, owner):
                    raise LeaseLost()
                last[0] = time.monotonic()

        try:
            execute(db, job, checkpoint)
            queue.finish(db, job.id, owner, "done")
            db.commit()
            log(logger, logging.INFO, "job done", job_id=str(job.id), kind=job.kind)
        except LeaseLost:
            db.rollback()
            log(logger, logging.WARNING, "lease lost; abandoning job", job_id=str(job.id))
        except TerminalJobError as exc:
            db.rollback()
            _fail(db, job, owner, "terminal_error", str(exc))
        except Exception as exc:  # noqa: BLE001 - bounded retry of unexpected failures
            db.rollback()
            logger.exception("job error")
            if queue.release_for_retry(db, job, owner, repr(exc)):
                db.commit()
                log(logger, logging.WARNING, "job released for retry", job_id=str(job.id), attempts=job.attempts)
            else:
                _fail(db, job, owner, "retries_exhausted", repr(exc))
        finally:
            hb_db.close()
        return True
    finally:
        db.close()


def _fail(db, job: JobLease, owner: str, code: str, detail: str) -> None:  # type: ignore[no-untyped-def]
    queue.finish(db, job.id, owner, "failed", f"{code}: {detail}"[:2000])
    if job.run_id is not None:
        transition(
            db,
            job.run_id,
            {"queued", "assessing", "planning", "simulating"},
            "failed",
            "worker",
            f"Run failed: {code}.",
            failure_code=code,
            failure_detail=detail[:4000],
        )
    db.commit()
    log(logger, logging.ERROR, "job failed", job_id=str(job.id), code=code)


def wait_for_schema(timeout_s: float = 120) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and not _stop.is_set():
        try:
            with SessionLocal() as db:
                db.execute(text("SELECT 1 FROM alembic_version")).first()
                return
        except Exception:  # noqa: BLE001 - database or migration not ready yet
            time.sleep(2)
    raise SystemExit("database schema not ready")


def main() -> None:
    configure_logging(settings.log_level)
    signal.signal(signal.SIGTERM, lambda *_: _stop.set())
    signal.signal(signal.SIGINT, lambda *_: _stop.set())
    wait_for_schema()
    owner = _owner()
    log(logger, logging.INFO, "worker started", owner=owner)
    while not _stop.is_set():
        try:
            if not process_one(owner):
                _stop.wait(settings.worker_poll_s)
        except Exception:  # noqa: BLE001 - keep the worker alive across transient DB errors
            logger.exception("worker loop error")
            _stop.wait(5)
    log(logger, logging.INFO, "worker stopped", owner=owner)


if __name__ == "__main__":
    main()
