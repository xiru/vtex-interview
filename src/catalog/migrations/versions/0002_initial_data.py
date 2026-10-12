"""Initial data: the house seller and (optionally) the one-time legacy SQLite load.

Revision ID: 0002_initial_data
Revises: 0001_schema

Without a legacy SQLite path this revision refuses to run unless ALLOW_EMPTY_CATALOG is set,
so a production database can never be initialized empty by accident (run make psqlmigrate).
This is a data migration and cannot be downgraded.
"""

from __future__ import annotations

import os

from alembic import context, op
from sqlalchemy import text

from catalog.config import HOUSE_SELLER_ID, get_settings, parse_bool
from catalog.legacy.sqlite_loader import load_legacy

revision = "0002_initial_data"
down_revision = "0001_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    attributes = context.config.attributes
    x_args = context.get_x_argument(as_dictionary=True)
    settings = get_settings()

    legacy_path = (
        attributes.get("legacy_sqlite")
        or x_args.get("legacy_sqlite")
        or os.environ.get("LEGACY_SQLITE_PATH")
    )
    allow_empty = attributes.get("allow_empty")
    if allow_empty is None:
        allow_empty = parse_bool(x_args.get("allow_empty", "0")) or settings.allow_empty_catalog

    if not legacy_path and not allow_empty:
        raise RuntimeError(
            "the catalog has not been initialized: run `make psqlmigrate` to load the legacy "
            "SQLite catalog first (or set ALLOW_EMPTY_CATALOG=1 for a deliberately empty catalog)"
        )

    bind = op.get_bind()
    bind.execute(
        text("INSERT INTO catalog.seller (id, name) VALUES (:id, :name)"),
        {"id": HOUSE_SELLER_ID, "name": settings.house_seller_name},
    )
    bind.execute(
        text("SELECT setval(pg_get_serial_sequence('catalog.seller', 'id'), :id)"),
        {"id": HOUSE_SELLER_ID},
    )
    if legacy_path:
        load_legacy(bind, legacy_path)


def downgrade() -> None:
    raise RuntimeError(
        "0002_initial_data is an irreversible data migration; to start over reset the "
        "database (make db-reset CONFIRM=yes) and run make psqlmigrate"
    )
