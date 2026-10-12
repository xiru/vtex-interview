"""Audit endpoints: imports, merges, undos, stats and health (GET only)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import Connection, func, select

from catalog.api.deps import Conn, Pages, Paging
from catalog.api.routes.catalog import key_equals, paginate
from catalog.api.schemas import (
    Health,
    ImportDetail,
    ImportSummary,
    MergeEntry,
    Page,
    Stats,
    UndoEntry,
)
from catalog.models import (
    brand,
    category,
    import_batch,
    merge_detail,
    operation,
    product,
    seller,
    seller_product,
    undo_detail,
)

router = APIRouter()


def _imports_query():
    return select(
        operation.c.id,
        operation.c.status,
        operation.c.started_at,
        operation.c.finished_at,
        operation.c.error_message,
        operation.c.summary,
        import_batch,
    ).join(import_batch, import_batch.c.operation_id == operation.c.id)


def _summary(row) -> ImportSummary:
    return ImportSummary.model_validate(row._mapping)


@router.get("/imports", response_model=Page[ImportSummary], tags=["imports"])
def list_imports(
    conn: Connection = Conn,
    page: Paging = Pages,
    md5: str | None = Query(None, description="full MD5 of the imported file"),
    seller_name: str | None = Query(None, alias="seller", description="seller in the file"),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    status: str | None = Query(None),
):
    query = _imports_query()
    if md5:
        query = query.where(import_batch.c.md5 == md5.strip().lower())
    if seller_name:
        canonical = conn.scalar(
            select(seller.c.name).where(key_equals(seller.c.name, seller_name))
        )
        if canonical is None:
            return Page[ImportSummary](items=[], total=0, limit=page.limit, offset=page.offset)
        query = query.where(operation.c.summary["per_seller"].has_key(canonical))
    if since:
        query = query.where(operation.c.started_at >= since)
    if until:
        query = query.where(operation.c.started_at < until)
    if status:
        query = query.where(operation.c.status == status)
    rows, total = paginate(conn, query, page, operation.c.id.desc())
    return Page[ImportSummary](
        items=[_summary(r) for r in rows], total=total, limit=page.limit, offset=page.offset
    )


@router.get("/imports/{operation_id}", response_model=ImportDetail, tags=["imports"])
def get_import(operation_id: int, conn: Connection = Conn):
    row = conn.execute(_imports_query().where(operation.c.id == operation_id)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="import not found")
    summary = row.summary or {}
    undos = (
        conn.execute(
            select(operation.c.id)
            .where(
                operation.c.kind == "undo", operation.c.summary["targets"].contains([operation_id])
            )
            .order_by(operation.c.id)
        )
        .scalars()
        .all()
    )
    return ImportDetail(
        **_summary(row).model_dump(),
        per_seller=summary.get("per_seller", {}),
        rejections=summary.get("rejections", []),
        undos=list(undos),
    )


@router.get("/merges", response_model=Page[MergeEntry], tags=["audit"])
def list_merges(conn: Connection = Conn, page: Paging = Pages):
    query = select(merge_detail, operation.c.kind, operation.c.status).join(
        operation, operation.c.id == merge_detail.c.operation_id
    )
    rows, total = paginate(conn, query, page, merge_detail.c.id.desc())
    return Page[MergeEntry](
        items=[MergeEntry.model_validate(r._mapping) for r in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/undos", response_model=Page[UndoEntry], tags=["audit"])
def list_undos(conn: Connection = Conn, page: Paging = Pages):
    query = select(undo_detail, operation.c.status, operation.c.summary).join(
        operation, operation.c.id == undo_detail.c.operation_id
    )
    rows, total = paginate(conn, query, page, undo_detail.c.operation_id.desc())
    items = [
        UndoEntry(
            operation_id=r.operation_id,
            status=r.status,
            undo_kind=r.undo_kind,
            criteria=r.criteria,
            changes_reverted=r.changes_reverted,
            conflicts_skipped=r.conflicts_skipped,
            targets=(r.summary or {}).get("targets", []),
            created=r.created,
        )
        for r in rows
    ]
    return Page[UndoEntry](items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/stats", response_model=Stats, tags=["audit"])
def stats(conn: Connection = Conn):
    def count(table):
        return int(conn.scalar(select(func.count()).where(table.c.active)) or 0)

    last = conn.execute(_imports_query().order_by(operation.c.id.desc()).limit(1)).first()
    return Stats(
        products=count(product),
        brands=count(brand),
        categories=count(category),
        sellers=count(seller),
        offers=count(seller_product),
        last_import=_summary(last) if last else None,
    )


@router.get("/health", response_model=Health, tags=["audit"])
def health(request: Request):
    from catalog import migrate

    state = request.app.state
    try:
        info = migrate.get_state(state.settings.api_database_url)
    except Exception:  # noqa: BLE001 - any failure means the database is unreachable
        raise HTTPException(status_code=503, detail="database unavailable") from None
    ok = info.at_head and (info.legacy_loaded or state.settings.allow_empty_catalog)
    payload = Health(
        status="ok" if ok else "degraded",
        database=True,
        schema_revision=info.current,
        schema_head=info.head,
        legacy_loaded=info.legacy_loaded,
    )
    if not ok:
        raise HTTPException(status_code=503, detail=payload.model_dump())
    return payload
