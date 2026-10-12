"""Manual merges of duplicate products, brands and categories.

A merge is an ``operation`` of kind ``merge`` (with ``merge_detail`` rows); every row it writes
goes through the change log, so ``catalog merge undo`` can reverse it with the undo engine.
Merged rows are never deleted: they become inactive and point at the kept row through
``merged_into_id``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import Connection, func, insert, select

from catalog.models import brand, category, merge_detail, product, product_category, seller_product
from catalog.services.changelog import ChangeRecorder
from catalog.services.operations import finish_operation, start_operation

ENTITY_TABLES = {"product": product, "brand": brand, "category": category}


class MergeError(RuntimeError):
    """Invalid merge request (unknown/inactive ids, merging a row into itself ...)."""


class MergeConflictError(RuntimeError):
    """A brand merge would create duplicate products; resolve them first or merge them too."""

    def __init__(self, pairs: list[tuple[int, int]]):
        self.pairs = pairs
        listing = ", ".join(f"keep {k} / duplicate {d}" for k, d in pairs[:20])
        super().__init__(
            f"{len(pairs)} product(s) would collide on name + brand ({listing}); "
            "merge those products first or pass --merge-products"
        )


@dataclass
class MergeResult:
    operation_id: int
    entity_type: str
    kept_id: int
    merged_ids: list[int]
    notes: list[str] = field(default_factory=list)


def _load_active(conn: Connection, entity: str, row_id: int):
    table = ENTITY_TABLES[entity]
    row = conn.execute(select(table).where(table.c.id == row_id)).first()
    if row is None:
        raise MergeError(f"{entity} {row_id} does not exist")
    if not row.active or row.merged_into_id is not None:
        raise MergeError(f"{entity} {row_id} is inactive or already merged")
    return row


def _validate(conn: Connection, entity: str, keep_id: int, duplicate_ids: list[int]):
    if not duplicate_ids:
        raise MergeError("at least one --duplicate is required")
    if keep_id in duplicate_ids:
        raise MergeError("--keep and --duplicate must be different")
    if len(set(duplicate_ids)) != len(duplicate_ids):
        raise MergeError("duplicate ids are repeated")
    kept = _load_active(conn, entity, keep_id)
    duplicates = [_load_active(conn, entity, d) for d in duplicate_ids]
    return kept, duplicates


def _start(conn: Connection, entity: str, keep_id: int, duplicate_ids: list[int]) -> int:
    op_id = start_operation(conn, "merge")
    for dup in duplicate_ids:
        conn.execute(
            insert(merge_detail).values(
                operation_id=op_id,
                entity_type=entity,
                kept_id=keep_id,
                merged_id=dup,
                origin="manual",
            )
        )
    return op_id


def _absorb_product(rec: ChangeRecorder, conn: Connection, kept, dup, notes: list[str]) -> None:
    """Move offers and categories of product ``dup`` onto ``kept``; deactivate ``dup``."""
    offers = conn.execute(
        select(seller_product)
        .where(seller_product.c.product_id == dup.id)
        .order_by(seller_product.c.id)
    ).all()
    for offer in offers:
        taken = conn.execute(
            select(seller_product.c.id).where(
                seller_product.c.seller_id == offer.seller_id,
                seller_product.c.product_id == kept.id,
            )
        ).first()
        if taken is None:
            rec.update(
                "seller_product", {"id": offer.id}, {"product_id": kept.id}, offer.seller_id
            )
        elif offer.active:
            rec.update("seller_product", {"id": offer.id}, {"active": False}, offer.seller_id)
            notes.append(
                f"seller {offer.seller_id} offers both products; offer {offer.id} was deactivated"
            )
    links = conn.execute(
        select(product_category).where(
            product_category.c.product_id == dup.id, product_category.c.active
        )
    ).all()
    for link in links:
        existing = conn.execute(
            select(product_category).where(
                product_category.c.product_id == kept.id,
                product_category.c.category_id == link.category_id,
            )
        ).first()
        pk = {"product_id": kept.id, "category_id": link.category_id}
        if existing is None:
            rec.insert("product_category", pk)
        elif not existing.active:
            rec.update("product_category", pk, {"active": True})
    if kept.brand_id is None and dup.brand_id is not None:
        clash = conn.execute(
            select(product.c.id).where(
                func.catalog.name_key(product.c.name) == func.catalog.name_key(kept.name),
                product.c.brand_id == dup.brand_id,
            )
        ).first()
        if clash is None:
            rec.update("product", {"id": kept.id}, {"brand_id": dup.brand_id})
    rec.update("product", {"id": dup.id}, {"active": False, "merged_into_id": kept.id})


def merge_products(conn: Connection, keep_id: int, duplicate_ids: list[int]) -> MergeResult:
    kept, duplicates = _validate(conn, "product", keep_id, duplicate_ids)
    op_id = _start(conn, "product", keep_id, duplicate_ids)
    rec = ChangeRecorder(conn, op_id)
    result = MergeResult(op_id, "product", keep_id, duplicate_ids)
    for dup in duplicates:
        kept = conn.execute(select(product).where(product.c.id == keep_id)).one()
        _absorb_product(rec, conn, kept, dup, result.notes)
    finish_operation(conn, op_id, "completed", summary={"notes": result.notes})
    return result


def merge_brands(
    conn: Connection, keep_id: int, duplicate_id: int, merge_colliding_products: bool = False
) -> MergeResult:
    _validate(conn, "brand", keep_id, [duplicate_id])
    moving = conn.execute(
        select(product).where(product.c.brand_id == duplicate_id).order_by(product.c.id)
    ).all()
    collisions: list[tuple[int, int]] = []
    for item in moving:
        twin = conn.execute(
            select(product.c.id).where(
                func.catalog.name_key(product.c.name) == func.catalog.name_key(item.name),
                product.c.brand_id == keep_id,
            )
        ).first()
        if twin is not None:
            collisions.append((int(twin.id), int(item.id)))
    if collisions and not merge_colliding_products:
        raise MergeConflictError(collisions)
    op_id = _start(conn, "brand", keep_id, [duplicate_id])
    rec = ChangeRecorder(conn, op_id)
    result = MergeResult(op_id, "brand", keep_id, [duplicate_id])
    colliding = {dup for _, dup in collisions}
    for kept_product_id, dup_product_id in collisions:
        kept_row = conn.execute(select(product).where(product.c.id == kept_product_id)).one()
        dup_row = conn.execute(select(product).where(product.c.id == dup_product_id)).one()
        if not dup_row.active or dup_row.merged_into_id is not None:
            continue
        _absorb_product(rec, conn, kept_row, dup_row, result.notes)
        conn.execute(
            insert(merge_detail).values(
                operation_id=op_id,
                entity_type="product",
                kept_id=kept_product_id,
                merged_id=dup_product_id,
                origin="manual",
            )
        )
        result.notes.append(f"product {dup_product_id} merged into {kept_product_id}")
    for item in moving:
        if item.id in colliding:
            continue
        rec.update("product", {"id": item.id}, {"brand_id": keep_id})
    rec.update("brand", {"id": duplicate_id}, {"active": False, "merged_into_id": keep_id})
    finish_operation(conn, op_id, "completed", summary={"notes": result.notes})
    return result


def merge_categories(conn: Connection, keep_id: int, duplicate_id: int) -> MergeResult:
    _validate(conn, "category", keep_id, [duplicate_id])
    op_id = _start(conn, "category", keep_id, [duplicate_id])
    rec = ChangeRecorder(conn, op_id)
    result = MergeResult(op_id, "category", keep_id, [duplicate_id])
    links = conn.execute(
        select(product_category).where(
            product_category.c.category_id == duplicate_id, product_category.c.active
        )
    ).all()
    for link in links:
        target = {"product_id": link.product_id, "category_id": keep_id}
        existing = conn.execute(
            select(product_category).where(
                product_category.c.product_id == link.product_id,
                product_category.c.category_id == keep_id,
            )
        ).first()
        if existing is None:
            rec.insert("product_category", target)
        elif not existing.active:
            rec.update("product_category", target, {"active": True})
        else:
            result.notes.append(f"product {link.product_id} already in category {keep_id}")
        rec.update(
            "product_category",
            {"product_id": link.product_id, "category_id": duplicate_id},
            {"active": False},
        )
    rec.update("category", {"id": duplicate_id}, {"active": False, "merged_into_id": keep_id})
    finish_operation(conn, op_id, "completed", summary={"notes": result.notes})
    return result
