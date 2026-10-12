"""Database engine helpers."""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine


def normalize_url(url: str) -> str:
    """Make plain ``postgresql://`` URLs use the psycopg (v3) driver."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


@lru_cache(maxsize=8)
def get_engine(url: str) -> Engine:
    """Return a cached engine for ``url``."""
    return create_engine(normalize_url(url), pool_pre_ping=True, future=True)
