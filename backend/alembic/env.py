"""Alembic environment. The database URL comes from TS_DATABASE_URL, never from this file."""

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import settings
from app.models import Base

config = context.config
config.set_main_option(
    "sqlalchemy.url", (config.attributes.get("database_url") or settings.database_url).replace("%", "%%")
)
target_metadata = Base.metadata


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
