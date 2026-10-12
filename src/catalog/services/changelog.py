"""Row writes that are recorded in ``catalog.change_log``.

Every insert/update of a tracked catalog row goes through :class:`ChangeRecorder`, which stores
the values written (and, for updates, the previous values). The undo engine relies on these
records to revert operations and to detect conflicts by comparing current values with the
values a change wrote.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Table, and_, insert, select, update
from sqlalchemy import Uuid as UuidType

from catalog.models import TRACKED_TABLES, change_log

#: Bookkeeping columns that are never logged nor compared.
UNTRACKED_COLUMNS = frozenset({"created", "updated", "last_operation_id"})


def to_json(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def from_json(table: Table, column: str, value: Any) -> Any:
    if value is not None and isinstance(table.c[column].type, UuidType):
        return uuid.UUID(value)
    return value


def pk_of(table: Table, row: Any) -> dict[str, Any]:
    return {col.name: row._mapping[col.name] for col in table.primary_key.columns}


def pk_clause(table: Table, pk: dict[str, Any]):
    return and_(*(table.c[name] == value for name, value in pk.items()))


def tracked_values(table: Table, row: Any) -> dict[str, Any]:
    return {
        col.name: to_json(row._mapping[col.name])
        for col in table.columns
        if col.name not in UNTRACKED_COLUMNS
    }


class ChangeRecorder:
    """Writes tracked rows on behalf of one operation and logs every change."""

    def __init__(self, conn: Connection, operation_id: int):
        self.conn = conn
        self.operation_id = operation_id

    def _log(
        self,
        table: Table,
        pk: dict[str, Any],
        action: str,
        old: dict[str, Any] | None,
        new: dict[str, Any],
        seller_id: int | None,
        reverts_change_id: int | None = None,
    ) -> int:
        change_id = self.conn.scalar(
            insert(change_log)
            .values(
                operation_id=self.operation_id,
                seller_id=seller_id,
                table_name=table.name,
                row_pk={k: to_json(v) for k, v in pk.items()},
                action=action,
                old_values=old,
                new_values=new,
                reverts_change_id=reverts_change_id,
            )
            .returning(change_log.c.id)
        )
        assert change_id is not None
        return int(change_id)

    def insert(self, table_name: str, values: dict[str, Any], seller_id: int | None = None):
        """Insert a row and log it; returns the inserted row (all columns)."""
        table = TRACKED_TABLES[table_name]
        row = self.conn.execute(insert(table).values(**values).returning(*table.columns)).one()
        new = tracked_values(table, row)
        pk = pk_of(table, row)
        attributed = seller_id
        if table_name == "seller" and attributed is None:
            attributed = row.id
        self._log(table, pk, "insert", None, new, attributed)
        return row

    def update(
        self,
        table_name: str,
        pk: dict[str, Any],
        changes: dict[str, Any],
        seller_id: int | None = None,
        reverts_change_id: int | None = None,
        log_noop: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> bool:
        """Update ``changes`` on one row; returns True when a value actually changed.

        ``extra`` holds untracked bookkeeping columns (e.g. ``last_operation_id``) that are
        written alongside a real change but never logged. ``log_noop`` records a change even
        when nothing differs (used by the undo engine for rows it deliberately keeps).
        """
        table = TRACKED_TABLES[table_name]
        current = self.conn.execute(
            select(table).where(pk_clause(table, pk)).with_for_update()
        ).one()
        old: dict[str, Any] = {}
        new: dict[str, Any] = {}
        for column, value in changes.items():
            before = to_json(current._mapping[column])
            after = to_json(value)
            if before != after or log_noop:
                old[column] = before
                new[column] = after
        changed = any(old[c] != new[c] for c in new)
        if not new:
            return False
        if changed:
            values = {c: from_json(table, c, v) for c, v in new.items()}
            if extra:
                values.update(extra)
            self.conn.execute(update(table).where(pk_clause(table, pk)).values(**values))
        self._log(table, pk, "update", old, new, seller_id, reverts_change_id)
        return changed
