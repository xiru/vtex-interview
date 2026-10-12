"""Shared pytest fixtures.

Integration tests need the throwaway PostgreSQL from ``compose.test.yaml`` (``make test`` starts
it). Tests under ``tests/unit`` never touch a database.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, text

from catalog import migrate
from catalog.config import get_settings
from helpers import SEED_PRODUCTS, make_legacy_sqlite

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
HOST = os.environ.get("TEST_DB_HOST", "127.0.0.1")
PORT = os.environ.get("TEST_DB_PORT", "55432")
PASSWORD = os.environ.get("TEST_DB_PASSWORD", "test")
ADMIN_URL = f"postgresql+psycopg://postgres:{PASSWORD}@{HOST}:{PORT}/catalog"

CATALOG_TABLES = (
    "change_log",
    "undo_detail",
    "merge_detail",
    "import_batch",
    "seller_product",
    "product_category",
    "product",
    "category",
    "brand",
    "seller",
    "operation",
)


def pytest_collection_modifyitems(config, items):
    for item in items:
        path = str(item.fspath)
        if f"{os.sep}integration{os.sep}" in path:
            item.add_marker(pytest.mark.integration)
        elif f"{os.sep}unit{os.sep}" in path:
            item.add_marker(pytest.mark.unit)


@pytest.fixture(autouse=True)
def _fresh_settings(monkeypatch):
    """Settings are cached; clear the cache so env changes in a test are honoured."""
    get_settings.cache_clear()
    monkeypatch.setenv("HOUSE_SELLER_NAME", "VTEX")
    yield
    get_settings.cache_clear()


@dataclass(frozen=True)
class DbUrls:
    owner: str
    reader: str


def owner_url(database: str) -> str:
    return f"postgresql+psycopg://catalog_owner:{PASSWORD}@{HOST}:{PORT}/{database}"


def reader_url(database: str) -> str:
    return f"postgresql+psycopg://catalog_reader:{PASSWORD}@{HOST}:{PORT}/{database}"


@pytest.fixture(scope="session")
def pg_admin() -> Iterator[Engine]:
    engine = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"test PostgreSQL not reachable at {HOST}:{PORT} ({exc}); run `make test`")
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def make_database(pg_admin):
    """Factory creating an empty database owned by catalog_owner; returns its URLs."""
    created: list[str] = []

    def factory(name: str) -> DbUrls:
        with pg_admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{name}" OWNER catalog_owner'))
        created.append(name)
        return DbUrls(owner_url(name), reader_url(name))

    yield factory
    with pg_admin.connect() as conn:
        for name in created:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture(scope="session")
def db_urls(make_database) -> DbUrls:
    urls = make_database("catalog_test")
    migrate.upgrade(urls.owner, allow_empty=True)
    return urls


@pytest.fixture(scope="session")
def engine(db_urls) -> Iterator[Engine]:
    eng = create_engine(db_urls.owner)
    yield eng
    eng.dispose()


@pytest.fixture(scope="session")
def reader_engine(db_urls) -> Iterator[Engine]:
    eng = create_engine(db_urls.reader)
    yield eng
    eng.dispose()


@pytest.fixture
def clean_db(engine) -> Engine:
    """Empty catalog with only the house seller; identities restart at 1."""
    with engine.begin() as conn:
        # Fail fast (instead of hanging) if a test leaked an open transaction.
        conn.execute(text("SET LOCAL lock_timeout = '5s'"))
        conn.execute(
            text(
                "TRUNCATE "
                + ", ".join(f"catalog.{t}" for t in CATALOG_TABLES)
                + " RESTART IDENTITY CASCADE"
            )
        )
        conn.execute(text("INSERT INTO catalog.seller (id, name) VALUES (1, 'VTEX')"))
        conn.execute(text("SELECT setval(pg_get_serial_sequence('catalog.seller','id'), 1)"))
    return engine


@pytest.fixture
def write_json(tmp_path):
    counter = {"n": 0}

    def writer(records: Any, name: str | None = None) -> Path:
        counter["n"] += 1
        path = tmp_path / (name or f"entries-{counter['n']}.json")
        path.write_text(json.dumps(records), encoding="utf-8")
        return path

    return writer


@pytest.fixture
def seeded(clean_db, tmp_path) -> Engine:
    """Clean catalog loaded with a small legacy catalog (through the real legacy loader)."""
    from catalog.legacy.sqlite_loader import load_legacy

    legacy = make_legacy_sqlite(tmp_path / "legacy.db", SEED_PRODUCTS)
    with clean_db.begin() as conn:
        load_legacy(conn, legacy)
    return clean_db
