"""Undo engine: import undo, merge undo (unmerge) and revert (undo of an undo).

Undoing a set of changes is itself an ``operation`` of kind ``undo``: it writes compensating
``change_log`` rows (``reverts_change_id`` points at the change it reverses) and marks the
reversed changes with ``reverted_by_operation_id``. Because a revert is just another undo,
any undo can be reverted, to any depth.

Conflicts are detected by value: a change can be reversed only if the row still holds exactly
the values that change wrote. Rows are never deleted; reversing an insert deactivates the row
(unless something active still references it, in which case it is kept and reported).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, exists, insert, select, update

from catalog.config import HOUSE_SELLER_ID
from catalog.models import (
    TRACKED_TABLES,
    change_log,
    import_batch,
    merge_detail,
    operation,
    product,
    product_category,
    seller_product,
    undo_detail,
)
from catalog.services.changelog import ChangeRecorder, pk_clause, to_json
from catalog.services.operations import finish_operation, refresh_status, start_operation

MD5_LENGTH = 32


class UndoError(RuntimeError):
    """The undo request is invalid (unknown hash, nothing to undo, wrong operation kind ...)."""


@dataclass(frozen=True)
class Conflict:
    change_id: int
    table: str
    row_pk: dict[str, Any]
    column: str | None
    expected: Any
    current: Any
    last_operation_id: int | None

    def describe(self) -> str:
        where = f"{self.table} {self.row_pk}"
        if self.column is None:
            return f"{where}: row not found"
        return (
            f"{where}.{self.column}: expected {self.expected!r}, found {self.current!r}"
            f" (last changed by operation {self.last_operation_id})"
        )


class UndoConflictError(RuntimeError):
    def __init__(self, conflicts: list[Conflict]):
        self.conflicts = conflicts
        lines = "\n  ".join(c.describe() for c in conflicts[:20])
        super().__init__(f"{len(conflicts)} conflicting change(s); nothing was undone:\n  {lines}")


@dataclass
class UndoResult:
    operation_id: int | None
    target_operations: list[int]
    reverted: int = 0
    kept: list[str] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    dry_run: bool = False


# --------------------------------------------------------------------------- helpers
def _still_referenced(conn: Connection, table: str, row_id: int) -> str | None:
    """Why an inserted row must stay active, or None when it can be deactivated."""
    if table == "seller" and row_id == HOUSE_SELLER_ID:
        return "house seller is never deactivated"
    if table == "product":
        offer = conn.execute(
            select(seller_product.c.seller_id).where(
                seller_product.c.product_id == row_id, seller_product.c.active
            )
        ).first()
        if offer is not None:
            return f"still offered by seller {offer.seller_id}"
    if table == "brand":
        if conn.scalar(select(exists().where(product.c.brand_id == row_id, product.c.active))):
            return "still used by active products"
    if table == "category":
        if conn.scalar(
            select(
                exists().where(product_category.c.category_id == row_id, product_category.c.active)
            )
        ):
            return "still linked to active products"
    if table == "seller":
        if conn.scalar(
            select(exists().where(seller_product.c.seller_id == row_id, seller_product.c.active))
        ):
            return "still has active offers"
    return None


def _last_operation(conn: Connection, table: str, row_pk: dict[str, Any]) -> int | None:
    return conn.scalar(
        select(change_log.c.operation_id)
        .where(change_log.c.table_name == table, change_log.c.row_pk == row_pk)
        .order_by(change_log.c.id.desc())
        .limit(1)
    )


def _check(conn: Connection, change) -> Conflict | None:
    table = TRACKED_TABLES[change.table_name]
    pk = change.row_pk
    current = conn.execute(select(table).where(pk_clause(table, pk)).with_for_update()).first()
    if current is None:
        return Conflict(change.id, change.table_name, pk, None, None, None, None)
    # Every column the change wrote must still hold the value it wrote; otherwise a later
    # operation (or a manual edit such as a seller rename) changed the row since.
    for column, expected in change.new_values.items():
        actual = to_json(current._mapping[column])
        if actual != expected:
            return Conflict(
                change.id,
                change.table_name,
                pk,
                column,
                expected,
                actual,
                _last_operation(conn, change.table_name, pk),
            )
    return None


def _set_reverted(conn: Connection, change_id: int, by: int | None) -> None:
    conn.execute(
        update(change_log).where(change_log.c.id == change_id).values(reverted_by_operation_id=by)
    )


def _mark_reverted(conn: Connection, change, by_operation: int) -> set[int]:
    """Mark ``change`` reversed and fix the effective state down its revert chain.

    In a chain c -> t -> s (c reverted t, t reverted s), effectiveness alternates: once c is
    reversed, t is effective again and s is again reversed by t's operation.
    Returns the operation ids whose status may have changed.
    """
    touched = {change.operation_id}
    _set_reverted(conn, change.id, by_operation)
    previous = change
    target_id = change.reverts_change_id
    make_effective = True
    while target_id is not None:
        target = conn.execute(select(change_log).where(change_log.c.id == target_id)).one()
        _set_reverted(conn, target.id, None if make_effective else previous.operation_id)
        touched.add(target.operation_id)
        previous = target
        target_id = target.reverts_change_id
        make_effective = not make_effective
    return touched


def _start_undo(conn: Connection, kind: str, criteria: dict[str, Any]) -> int:
    op_id = start_operation(conn, "undo")
    conn.execute(insert(undo_detail).values(operation_id=op_id, undo_kind=kind, criteria=criteria))
    return op_id


def revert_changes(
    conn: Connection,
    changes: list[Any],
    undo_kind: str,
    criteria: dict[str, Any],
    skip_conflicts: bool,
) -> UndoResult:
    """Reverse ``changes`` (newest first) inside the caller's transaction."""
    op_id = _start_undo(conn, undo_kind, criteria)
    recorder = ChangeRecorder(conn, op_id)
    result = UndoResult(
        operation_id=op_id, target_operations=sorted({c.operation_id for c in changes})
    )
    touched: set[int] = set()
    for change in sorted(changes, key=lambda c: c.id, reverse=True):
        conflict = _check(conn, change)
        if conflict is not None:
            if not skip_conflicts:
                raise UndoConflictError([conflict])
            result.conflicts.append(conflict)
            continue
        if change.action == "update":
            restore = {column: change.old_values[column] for column in change.new_values}
            recorder.update(
                change.table_name,
                change.row_pk,
                restore,
                seller_id=change.seller_id,
                reverts_change_id=change.id,
                log_noop=True,
            )
        else:
            row_id = change.row_pk.get("id")
            reason = _still_referenced(conn, change.table_name, row_id) if row_id else None
            if reason:
                result.kept.append(f"{change.table_name} {change.row_pk}: {reason}")
            recorder.update(
                change.table_name,
                change.row_pk,
                {"active": bool(reason)},
                seller_id=change.seller_id,
                reverts_change_id=change.id,
                log_noop=True,
            )
        touched |= _mark_reverted(conn, change, op_id)
        result.reverted += 1
    conn.execute(
        update(undo_detail)
        .where(undo_detail.c.operation_id == op_id)
        .values(changes_reverted=result.reverted, conflicts_skipped=len(result.conflicts))
    )
    finish_operation(
        conn,
        op_id,
        "completed",
        summary={
            "kept": result.kept[:200],
            "conflicts": [c.describe() for c in result.conflicts[:200]],
            "targets": result.target_operations,
        },
    )
    for touched_op in sorted(touched):
        refresh_status(conn, touched_op)
    return result


def _effective_changes(conn: Connection, op_ids: list[int], extra=()) -> list[Any]:
    query = select(change_log).where(
        change_log.c.operation_id.in_(op_ids),
        change_log.c.reverted_by_operation_id.is_(None),
        *extra,
    )
    return list(conn.execute(query.order_by(change_log.c.id)).all())


# --------------------------------------------------------------------------- import undo
def validate_md5(value: str) -> str:
    text = value.strip().lower()
    if len(text) != MD5_LENGTH or any(ch not in "0123456789abcdef" for ch in text):
        raise UndoError(f"invalid MD5 {value!r}: a full 32-character hex digest is required")
    return text


def undo_imports(
    conn: Connection,
    md5s: list[str] | None = None,
    all_files: bool = False,
    seller_id: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    skip_conflicts: bool = False,
) -> UndoResult:
    if bool(md5s) == all_files:
        raise UndoError("choose exactly one of --md5 or --all-files")
    if all_files and since is None:
        raise UndoError("--all-files requires --since")
    filters = []
    if seller_id is not None:
        filters.append(change_log.c.seller_id == seller_id)
    if since is not None:
        filters.append(change_log.c.created >= since)
    if until is not None:
        filters.append(change_log.c.created < until)

    if md5s:
        hashes = [validate_md5(m) for m in md5s]
        op_ids: list[int] = []
        for digest in hashes:
            found = (
                conn.execute(
                    select(operation.c.id)
                    .join(import_batch, import_batch.c.operation_id == operation.c.id)
                    .where(import_batch.c.md5 == digest, operation.c.kind == "import")
                )
                .scalars()
                .all()
            )
            if not found:
                raise UndoError(f"no import found for MD5 {digest}")
            op_ids.extend(found)
    else:
        op_ids = list(
            conn.execute(
                select(operation.c.id).where(
                    operation.c.kind == "import",
                    operation.c.status.in_(("completed", "partially_undone")),
                )
            ).scalars()
        )
    changes = _effective_changes(conn, op_ids, filters)
    if not changes:
        raise UndoError("nothing to undo for the given criteria")
    criteria = {
        "md5": md5s and [validate_md5(m) for m in md5s],
        "all_files": all_files,
        "seller_id": seller_id,
        "since": since.isoformat() if since else None,
        "until": until.isoformat() if until else None,
    }
    return revert_changes(conn, changes, "import_undo", criteria, skip_conflicts)


# --------------------------------------------------------------------------- revert / unmerge
def revert_undo(conn: Connection, undo_operation_id: int) -> UndoResult:
    """Undo an undo (all-or-nothing)."""
    op = conn.execute(select(operation).where(operation.c.id == undo_operation_id)).first()
    if op is None or op.kind != "undo":
        raise UndoError(f"operation {undo_operation_id} is not an undo operation")
    changes = _effective_changes(conn, [undo_operation_id])
    if not changes:
        raise UndoError(f"undo {undo_operation_id} has nothing left to revert")
    return revert_changes(
        conn, changes, "revert", {"target_operation_id": undo_operation_id}, skip_conflicts=False
    )


def undo_merge(conn: Connection, merge_operation_id: int) -> UndoResult:
    """Unmerge (all-or-nothing). Legacy-load merges cannot be undone."""
    op = conn.execute(select(operation).where(operation.c.id == merge_operation_id)).first()
    if op is None or op.kind != "merge":
        raise UndoError(
            f"operation {merge_operation_id} is not a manual merge "
            "(legacy-load merges cannot be undone)"
        )
    changes = _effective_changes(conn, [merge_operation_id])
    if not changes:
        raise UndoError(f"merge {merge_operation_id} is already undone")
    result = revert_changes(
        conn,
        changes,
        "merge_undo",
        {"target_operation_id": merge_operation_id},
        skip_conflicts=False,
    )
    kept_ids = (
        conn.execute(
            select(merge_detail.c.kept_id).where(
                merge_detail.c.operation_id == merge_operation_id,
                merge_detail.c.entity_type == "product",
            )
        )
        .scalars()
        .all()
    )
    if kept_ids and op.finished_at is not None:
        later = conn.execute(
            select(seller_product.c.id, seller_product.c.product_id).where(
                seller_product.c.product_id.in_(kept_ids),
                seller_product.c.created > op.finished_at,
            )
        ).all()
        for row in later:
            result.notes.append(
                f"offer {row.id} on kept product {row.product_id} was created after the merge "
                "and stays on the kept product"
            )
    return result
