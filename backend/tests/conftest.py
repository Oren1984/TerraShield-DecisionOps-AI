"""Shared fixtures.

DB tests need TS_TEST_DATABASE_URL pointing at a disposable PostgreSQL database
with pgvector (for example the one started by scripts/test-backend.sh). The URL
is swapped in before the app modules create their engine.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

TEST_DB = os.environ.get("TS_TEST_DATABASE_URL")
if TEST_DB:
    os.environ["TS_DATABASE_URL"] = TEST_DB
os.environ.setdefault("TS_SIM_STEP_DELAY_MS", "0")
os.environ.setdefault("TS_LOG_LEVEL", "WARNING")
os.environ.setdefault("TS_SENSITIVITY_SAMPLES", "4")

from app.simulation.district import load_district  # noqa: E402
from app.simulation.scenario import ScenarioParams  # noqa: E402

SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios" / "seeded"


def load_scenario(slug_prefix: str) -> tuple[dict, ScenarioParams]:
    path = next(p for p in SCENARIOS.glob("*.json") if p.name.startswith(slug_prefix))
    data = json.loads(path.read_text(encoding="utf-8"))
    return data, ScenarioParams.model_validate(data["params"])


@pytest.fixture(scope="session")
def district():
    return load_district()


@pytest.fixture
def coastal():
    return load_scenario("01")[1]


@pytest.fixture
def windy():
    return load_scenario("02")[1]


@pytest.fixture
def cascade():
    return load_scenario("03")[1]


@pytest.fixture
def conflict():
    return load_scenario("04")[1]


# ----------------------------------------------------------------------------- DB


@pytest.fixture(scope="session")
def migrated() -> Iterator[None]:
    if not TEST_DB:
        pytest.skip("TS_TEST_DATABASE_URL not set")
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from app.db import engine

    with engine.begin() as c:
        c.execute(text("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;"))
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    command.upgrade(cfg, "head")
    from app.db import session_scope
    from app.seed import seed_all

    with session_scope() as db:
        seed_all(db, demo=False)
    yield


@pytest.fixture
def db(migrated):
    from app.db import SessionLocal

    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
