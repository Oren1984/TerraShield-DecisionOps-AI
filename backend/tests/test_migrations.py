"""Migrations: upgrade/downgrade round trip, no model drift, pgvector available."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text

from app.models import Base

pytestmark = pytest.mark.db

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def mig_url():
    url = os.environ.get("TS_MIGRATION_TEST_DATABASE_URL")
    if not url:
        pytest.skip("TS_MIGRATION_TEST_DATABASE_URL not set")
    return url


def _cfg(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.attributes["database_url"] = url
    return cfg


def test_round_trip_and_no_drift(mig_url):
    eng = create_engine(mig_url)
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;"))
    cfg = _cfg(mig_url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    with eng.connect() as c:
        tables = c.execute(
            text(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' "
                "AND table_name <> 'alembic_version'"
            )
        ).scalar()
    assert tables == 0
    command.upgrade(cfg, "head")
    with eng.connect() as c:
        assert c.execute(text("SELECT extname FROM pg_extension WHERE extname='vector'")).scalar() == "vector"
        diff = compare_metadata(MigrationContext.configure(c), Base.metadata)
    assert diff == [], diff
    eng.dispose()


def test_failed_migration_rolls_back(mig_url):
    """A failing statement inside a migration transaction leaves no partial schema."""
    eng = create_engine(mig_url)
    count = text("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
    with eng.connect() as c:
        before = c.execute(count).scalar()
    with pytest.raises(Exception), eng.begin() as c:  # noqa: B017 - any database error
        c.execute(text("CREATE TABLE half_done (id int)"))
        c.execute(text("ALTER TABLE does_not_exist ADD COLUMN x int"))
    with eng.connect() as c:
        after = c.execute(count).scalar()
    assert before == after
    eng.dispose()
