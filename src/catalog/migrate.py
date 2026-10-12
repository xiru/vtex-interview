"""Programmatic Alembic runner used by ``catalog db …`` and by the tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, inspect, pool, text

from catalog.db import normalize_url

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
INITIAL_DATA_REVISION = "0002_initial_data"


class MigrationError(RuntimeError):
    """Raised when the database is not in a state the requested migration can handle."""


@dataclass(frozen=True)
class MigrationState:
    current: str | None
    head: str
    legacy_loaded: bool

    @property
    def at_head(self) -> bool:
        return self.current == self.head

    @property
    def initialized(self) -> bool:
        """True once the initial-data revision (house seller, optional legacy load) ran."""
        return self.current is not None and self.current != "0001_schema"


def alembic_config(
    database_url: str,
    legacy_sqlite: str | None = None,
    allow_empty: bool | None = None,
) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.attributes["database_url"] = normalize_url(database_url)
    cfg.attributes["legacy_sqlite"] = legacy_sqlite
    cfg.attributes["allow_empty"] = allow_empty
    return cfg


def _engine(database_url: str) -> Engine:
    return create_engine(normalize_url(database_url), poolclass=pool.NullPool)


def get_state(database_url: str) -> MigrationState:
    head = ScriptDirectory.from_config(alembic_config(database_url)).get_current_head()
    assert head is not None
    engine = _engine(database_url)
    try:
        with engine.connect() as conn:
            current = MigrationContext.configure(conn).get_current_revision()
            legacy = False
            if inspect(conn).has_table("operation", schema="catalog"):
                legacy = bool(
                    conn.scalar(
                        text(
                            "SELECT count(*) FROM catalog.operation "
                            "WHERE kind = 'legacy_load' AND status = 'completed'"
                        )
                    )
                )
    finally:
        engine.dispose()
    return MigrationState(current=current, head=head, legacy_loaded=legacy)


def upgrade(
    database_url: str,
    revision: str = "head",
    legacy_sqlite: str | None = None,
    allow_empty: bool | None = None,
) -> str:
    """Upgrade the schema; returns a human readable outcome.

    With ``legacy_sqlite`` the initial-data revision loads the legacy SQLite catalog. Running it
    again on an already loaded database is a no-op; a database that was initialized *without*
    the legacy data is refused, because the load can only happen into an empty catalog.
    """
    state = get_state(database_url)
    if legacy_sqlite:
        if not Path(legacy_sqlite).is_file():
            raise MigrationError(f"legacy SQLite file not found: {legacy_sqlite}")
        if state.initialized and not state.legacy_loaded:
            raise MigrationError(
                "this database was initialized without the legacy catalog "
                "(ALLOW_EMPTY_CATALOG); the legacy load only runs on a new database. "
                "Reset the database (make db-reset CONFIRM=yes) and run make psqlmigrate again."
            )
        if state.legacy_loaded and state.at_head:
            return "already migrated: the legacy catalog is loaded and the schema is at head"
    command.upgrade(alembic_config(database_url, legacy_sqlite, allow_empty), revision)
    new_state = get_state(database_url)
    return f"database at revision {new_state.current}"


def downgrade(database_url: str, revision: str) -> str:
    command.downgrade(alembic_config(database_url), revision)
    return f"database at revision {get_state(database_url).current}"


def history(database_url: str) -> list[str]:
    script = ScriptDirectory.from_config(alembic_config(database_url))
    return [f"{rev.revision}: {rev.doc}" for rev in reversed(list(script.walk_revisions()))]
