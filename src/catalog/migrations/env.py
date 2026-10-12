"""Alembic environment: PostgreSQL only, URL taken from the runner or from settings."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from catalog.config import get_settings
from catalog.db import normalize_url

config = context.config


def _database_url() -> str:
    return normalize_url(config.attributes.get("database_url") or get_settings().database_url)


def run_migrations_offline() -> None:
    context.configure(url=_database_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as conn:
        context.configure(connection=conn)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
