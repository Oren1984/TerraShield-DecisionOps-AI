"""Explicit run lifecycle with compare-and-set transitions.

draft -> validated -> queued -> assessing -> planning -> awaiting_review -> approved -> simulating -> completed
plus rejected, blocked, failed and cancelled. A transition only succeeds if the
run is still in one of the expected source states, which prevents duplicate or
racing transitions (for example cancel vs. completion).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.audit import trace
from app.models import Run

TRANSITIONS: dict[str, set[str]] = {
    "draft": {"validated", "failed"},
    "validated": {"queued"},
    "queued": {"assessing", "planning", "cancelled", "failed"},
    "assessing": {"assessing", "planning", "cancelled", "failed", "blocked"},
    "planning": {
        "assessing",
        "planning",
        "awaiting_review",
        "blocked",
        "cancelled",
        "failed",
    },  # assessing: crash recovery
    "awaiting_review": {"approved", "rejected", "queued", "cancelled"},
    "approved": {"simulating", "blocked", "cancelled"},
    "simulating": {"simulating", "completed", "cancelled", "failed"},
}
TERMINAL = {"completed", "rejected", "blocked", "failed", "cancelled"}
ACTIVE = {"queued", "assessing", "planning", "simulating"}
CANCELLABLE = {"queued", "assessing", "planning", "awaiting_review", "approved", "simulating"}


class TransitionError(Exception):
    pass


def transition(
    db: Session,
    run_id: uuid.UUID,
    sources: set[str],
    target: str,
    actor: str = "system",
    message: str = "",
    **fields: object,
) -> bool:
    """Atomically move a run from any of ``sources`` to ``target``. Returns False if the run moved on."""
    for s in sources:
        if target not in TRANSITIONS.get(s, set()):
            raise TransitionError(f"illegal transition {s} -> {target}")
    now = datetime.now(UTC)
    values: dict[str, object] = {"status": target, "updated_at": now, **fields}
    if target in TERMINAL:
        values["finished_at"] = now
    result = db.execute(
        update(Run).where(Run.id == run_id, Run.status.in_(sources)).values(**values).returning(Run.id)
    ).first()
    if result is None:
        return False
    trace(db, run_id, actor, "status_changed", message or f"Run is now {target}.", status=target)
    return True
