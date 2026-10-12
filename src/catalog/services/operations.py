"""Operations (import, merge, undo, legacy load), their statuses and the write lock."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection, func, insert, select, text, update

from catalog.models import change_log, operation

#: Advisory lock key shared by every write command, so writers never interleave.
WRITE_LOCK_KEY = 0x0CA7A106


class LockBusyError(RuntimeError):
    """Another write operation holds the catalog write lock (used with --no-wait)."""


def acquire_write_lock(conn: Connection, wait: bool = True) -> None:
    """Take the transaction-scoped catalog write lock."""
    if wait:
        conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": WRITE_LOCK_KEY})
        return
    if not conn.scalar(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": WRITE_LOCK_KEY}):
        raise LockBusyError("another write operation is running (try again, or drop --no-wait)")


def start_operation(conn: Connection, kind: str, status: str = "running") -> int:
    op_id = conn.scalar(
        insert(operation).values(kind=kind, status=status).returning(operation.c.id)
    )
    assert op_id is not None
    return int(op_id)


def finish_operation(
    conn: Connection,
    op_id: int,
    status: str,
    summary: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    values: dict[str, Any] = {"status": status, "finished_at": func.now()}
    if summary is not None:
        values["summary"] = summary
    if error is not None:
        values["error_message"] = error[:2000]
    conn.execute(update(operation).where(operation.c.id == op_id).values(**values))


def refresh_status(conn: Connection, op_id: int) -> str | None:
    """Recompute an operation's status from how many of its changes are still effective."""
    row = conn.execute(
        select(operation.c.kind, operation.c.status).where(operation.c.id == op_id)
    ).one()
    if row.status in ("running", "failed") or row.kind == "legacy_load":
        return row.status
    total, effective = conn.execute(
        select(
            func.count(),
            func.count().filter(change_log.c.reverted_by_operation_id.is_(None)),
        ).where(change_log.c.operation_id == op_id)
    ).one()
    if total == 0:
        return row.status
    if row.kind == "undo":
        names = ("completed", "reverted", "partially_reverted")
    else:
        names = ("completed", "undone", "partially_undone")
    if effective == total:
        status = names[0]
    elif effective == 0:
        status = names[1]
    else:
        status = names[2]
    if status != row.status:
        conn.execute(update(operation).where(operation.c.id == op_id).values(status=status))
    return status
