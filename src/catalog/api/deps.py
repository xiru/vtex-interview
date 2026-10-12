"""FastAPI dependencies: database connection and pagination."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Depends, Query, Request
from sqlalchemy import Connection

DEFAULT_LIMIT = 100
MAX_LIMIT = 1000


def get_conn(request: Request) -> Iterator[Connection]:
    """A read-only connection (the API runs as the catalog_reader role)."""
    with request.app.state.engine.connect() as conn:
        yield conn


@dataclass(frozen=True)
class Paging:
    limit: int
    offset: int


def paging(
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT, description="page size (1-1000)"),
    offset: int = Query(0, ge=0, description="number of records to skip"),
) -> Paging:
    return Paging(limit, offset)


def show_inactive_flag(
    show_inactive: bool = Query(False, description="include deactivated/merged records"),
) -> bool:
    return show_inactive


Conn = Depends(get_conn)
Pages = Depends(paging)
ShowInactive = Depends(show_inactive_flag)
