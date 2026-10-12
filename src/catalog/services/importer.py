"""Import of seller catalog files (JSON array of records).

Every record names its own seller, so one file may contain several sellers. Each import is an
``operation`` of kind ``import`` with an ``import_batch`` row (file name, MD5, size, counters).
All catalog writes go through :class:`ChangeRecorder` so the import can be undone later.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, func, insert, select, text, update

from catalog.config import IdPolicy
from catalog.logconfig import safe_repr
from catalog.models import (
    brand,
    category,
    import_batch,
    operation,
    product,
    product_category,
    seller,
    seller_product,
)
from catalog.services.changelog import ChangeRecorder
from catalog.services.operations import acquire_write_lock, finish_operation, start_operation
from catalog.validation import Rejection, ValidRecord, validate_record

log = logging.getLogger("catalog.import")

COUNTERS = (
    "total_records",
    "invalid_records",
    "skipped_records",
    "sellers_created",
    "products_created",
    "products_updated",
    "duplicate_products",
    "offers_created",
    "offers_updated",
    "unchanged",
)


class ImportFileError(RuntimeError):
    """The file cannot be imported at all (unreadable, not a JSON array, ...)."""


class DuplicateFileError(RuntimeError):
    """A file with the same MD5 is already effective; use force to import it again."""


@dataclass
class ImportOptions:
    policy: IdPolicy = field(default_factory=IdPolicy)
    dry_run: bool = False
    force: bool = False
    create_sellers: bool = True
    wait: bool = True
    now: datetime | None = None


@dataclass
class ImportResult:
    operation_id: int | None
    md5: str
    file_size: int
    counters: Counter[str]
    per_seller: dict[str, Counter[str]]
    rejections: list[tuple[int, Rejection]]
    dry_run: bool


def file_digest(path: str | Path) -> tuple[str, int]:
    """MD5 (lowercase hex) and size of a file, streamed."""
    digest = hashlib.md5(usedforsecurity=False)
    size = 0
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def load_records(path: str | Path) -> list[Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImportFileError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, list):
        raise ImportFileError("the file must contain a JSON array of records")
    return data


def _key_map(conn: Connection, values: set[str]) -> dict[str, str]:
    """Database-computed name keys (the single normalization algorithm) for many strings."""
    if not values:
        return {}
    rows = conn.execute(
        text("SELECT v, catalog.name_key(v) AS k FROM unnest(CAST(:vals AS text[])) AS v"),
        {"vals": sorted(values)},
    )
    return {row.v: row.k for row in rows}


class _Importer:
    def __init__(self, conn: Connection, op_id: int, options: ImportOptions):
        self.conn = conn
        self.op_id = op_id
        self.options = options
        self.rec = ChangeRecorder(conn, op_id)
        self.counters: Counter[str] = Counter({name: 0 for name in COUNTERS})
        self.per_seller: dict[str, Counter[str]] = defaultdict(Counter)
        self.rejections: list[tuple[int, Rejection]] = []

    # ------------------------------------------------------------------ lookups
    def _by_key(self, table, name: str):
        return self.conn.execute(
            select(table).where(func.catalog.name_key(table.c.name) == func.catalog.name_key(name))
        ).first()

    def _survivor(self, table, row):
        """Follow merged_into_id to the surviving row."""
        seen = set()
        while row is not None and row.merged_into_id is not None and row.id not in seen:
            seen.add(row.id)
            row = self.conn.execute(select(table).where(table.c.id == row.merged_into_id)).one()
        return row

    def _reactivate(self, table_name: str, pk: dict[str, Any], seller_id: int | None) -> None:
        self.rec.update(table_name, pk, {"active": True}, seller_id=seller_id)

    # ------------------------------------------------------------------ entities
    def _resolve_named(self, table, name: str, seller_id: int | None, display: Any) -> int:
        row = self._by_key(table, name)
        if row is not None:
            row = self._survivor(table, row)
            if not row.active:
                self._reactivate(table.name, {"id": row.id}, seller_id)
            return int(row.id)
        created = self.rec.insert(table.name, {"name": display}, seller_id=seller_id)
        return int(created.id)

    def _find_product(self, name: str, brand_id: int | None, brand_name: str | None):
        """Return (product_row | None, ambiguous: bool) following the matching rules."""
        rows = self.conn.execute(
            select(product).where(
                func.catalog.name_key(product.c.name) == func.catalog.name_key(name)
            )
        ).all()
        if not rows:
            return None, False
        if brand_name is not None:
            if brand_id is not None:
                exact = [r for r in rows if r.brand_id == brand_id]
                if exact:
                    return self._survivor(product, exact[0]), False
            unbranded = [r for r in rows if r.brand_id is None]
            if unbranded:
                return self._survivor(product, unbranded[0]), False
            return None, False
        unbranded = [r for r in rows if r.brand_id is None]
        if unbranded:
            return self._survivor(product, unbranded[0]), False
        survivors = {self._survivor(product, r).id: self._survivor(product, r) for r in rows}
        if len(survivors) == 1:
            return next(iter(survivors.values())), False
        return None, True

    # ------------------------------------------------------------------ records
    def skip(self, index: int, rejection: Rejection, seller_name: str | None = None) -> None:
        self.rejections.append((index, rejection))
        self.counters["skipped_records"] += 1
        if seller_name:
            self.per_seller[seller_name]["skipped_records"] += 1
        log.warning("record %d skipped (seller=%s): %s", index, safe_repr(seller_name), rejection)

    def process(self, index: int, rec: ValidRecord) -> None:
        conn = self.conn
        tally = self.per_seller[rec.seller_name]

        # Read-only lookups first, so a skipped record leaves no trace.
        seller_row = self._by_key(seller, rec.seller_name)
        if seller_row is None and not self.options.create_sellers:
            self.skip(
                index,
                Rejection("UNKNOWN_SELLER", "SellerName", "seller does not exist"),
                rec.seller_name,
            )
            return
        brand_row = self._by_key(brand, rec.brand) if rec.brand else None
        brand_row = self._survivor(brand, brand_row) if brand_row is not None else None
        target, ambiguous = self._find_product(
            rec.name, brand_row.id if brand_row is not None else None, rec.brand
        )
        if ambiguous:
            self.skip(
                index,
                Rejection(
                    "AMBIGUOUS_MATCH",
                    "Name",
                    "no brand given and several products with different brands match",
                ),
                rec.seller_name,
            )
            return
        existing_offer = None
        if seller_row is not None:
            existing_offer = conn.execute(
                select(seller_product).where(
                    seller_product.c.seller_id == seller_row.id,
                    seller_product.c.seller_product_id == rec.seller_product_id,
                )
            ).first()
            if target is not None:
                clash = conn.execute(
                    select(seller_product.c.id).where(
                        seller_product.c.seller_id == seller_row.id,
                        seller_product.c.product_id == target.id,
                    )
                ).first()
                if clash is not None and (existing_offer is None or clash.id != existing_offer.id):
                    self.skip(
                        index,
                        Rejection(
                            "DUPLICATE_OFFER",
                            "Id",
                            "the seller already offers this product under another id",
                        ),
                        rec.seller_name,
                    )
                    return

        # Writes.
        if seller_row is None:
            created = self.rec.insert("seller", {"name": rec.seller_name})
            seller_id = int(created.id)
            self.counters["sellers_created"] += 1
            tally["sellers_created"] += 1
        else:
            seller_id = int(seller_row.id)
            if not seller_row.active:
                self._reactivate("seller", {"id": seller_id}, seller_id)

        brand_id = None
        if rec.brand:
            brand_id = self._resolve_named(brand, rec.brand, seller_id, rec.brand)
        category_id = None
        if rec.category:
            category_id = self._resolve_named(
                category, rec.category, seller_id, func.lower(rec.category)
            )

        enriched = False
        if target is None:
            created_product = self.rec.insert(
                "product", {"name": rec.name, "brand_id": brand_id}, seller_id=seller_id
            )
            product_id = int(created_product.id)
            self.counters["products_created"] += 1
            tally["products_created"] += 1
        else:
            product_id = int(target.id)
            self.counters["duplicate_products"] += 1
            tally["duplicate_products"] += 1
            changes: dict[str, Any] = {}
            if not target.active:
                changes["active"] = True
            if (
                target.brand_id is None
                and brand_id is not None
                and self._brand_free(target.name, brand_id)
            ):
                changes["brand_id"] = brand_id
            if changes:
                enriched |= self.rec.update("product", {"id": product_id}, changes, seller_id)

        if category_id is not None:
            enriched |= (
                self._link_category(product_id, category_id, seller_id) and target is not None
            )
        if enriched:
            self.counters["products_updated"] += 1
            tally["products_updated"] += 1

        offer_values = {
            "product_id": product_id,
            "name": rec.name,
            "brand_name": rec.brand,
            "category_name": rec.category,
            "active": True,
        }
        if existing_offer is None:
            self.rec.insert(
                "seller_product",
                {
                    **offer_values,
                    "seller_id": seller_id,
                    "seller_product_id": rec.seller_product_id,
                    "last_operation_id": self.op_id,
                },
                seller_id=seller_id,
            )
            self.counters["offers_created"] += 1
            tally["offers_created"] += 1
        elif self.rec.update(
            "seller_product",
            {"id": existing_offer.id},
            offer_values,
            seller_id=seller_id,
            extra={"last_operation_id": self.op_id},
        ):
            self.counters["offers_updated"] += 1
            tally["offers_updated"] += 1
        else:
            self.counters["unchanged"] += 1
            tally["unchanged"] += 1

    def _brand_free(self, product_name: str, brand_id: int) -> bool:
        """True when giving ``product_name`` this brand would not violate uniqueness."""
        return (
            self.conn.execute(
                select(product.c.id).where(
                    func.catalog.name_key(product.c.name) == func.catalog.name_key(product_name),
                    product.c.brand_id == brand_id,
                )
            ).first()
            is None
        )

    def _link_category(self, product_id: int, category_id: int, seller_id: int) -> bool:
        """Ensure an active product/category link; True when something changed."""
        pk = {"product_id": product_id, "category_id": category_id}
        link = self.conn.execute(
            select(product_category).where(
                product_category.c.product_id == product_id,
                product_category.c.category_id == category_id,
            )
        ).first()
        if link is None:
            self.rec.insert("product_category", pk, seller_id=seller_id)
            return True
        if not link.active:
            return self.rec.update("product_category", pk, {"active": True}, seller_id)
        return False


def _validate_all(
    conn: Connection, records: list[Any], options: ImportOptions
) -> tuple[list[tuple[int, ValidRecord]], list[tuple[int, Rejection, str | None]]]:
    valid: list[tuple[int, ValidRecord]] = []
    invalid: list[tuple[int, Rejection, str | None]] = []
    for index, raw in enumerate(records):
        result = validate_record(raw, options.policy, options.now)
        if isinstance(result, Rejection):
            seller_name = raw.get("SellerName") if isinstance(raw, dict) else None
            invalid.append((index, result, seller_name if isinstance(seller_name, str) else None))
        else:
            valid.append((index, result))
    # EMPTY_KEY: names without latin letters/digits, checked with the database key function.
    strings = {
        value
        for _, rec in valid
        for value in (rec.seller_name, rec.name, rec.brand, rec.category)
        if value
    }
    keys = _key_map(conn, strings)
    kept: list[tuple[int, ValidRecord]] = []
    for index, rec in valid:
        empty = [
            label
            for label, value in (
                ("SellerName", rec.seller_name),
                ("Name", rec.name),
                ("Brand", rec.brand),
                ("Category", rec.category),
            )
            if value and keys.get(value) == ""
        ]
        if empty:
            invalid.append(
                (
                    index,
                    Rejection("EMPTY_KEY", empty[0], "no latin letters or digits"),
                    rec.seller_name,
                )
            )
        else:
            kept.append((index, rec))
    return kept, invalid


def _record_failure(engine: Engine, op_id: int, error: str) -> None:
    with engine.begin() as conn:
        finish_operation(conn, op_id, "failed", error=error)


def run_import(
    engine: Engine, path: str | Path, options: ImportOptions | None = None
) -> ImportResult:
    """Import ``path``; see the module docstring. Raises on fatal errors (recorded as failed)."""
    options = options or ImportOptions()
    path = Path(path)
    if not path.is_file():
        raise ImportFileError(f"file not found: {path}")
    md5, size = file_digest(path)

    def create_operation(conn: Connection) -> int:
        op_id = start_operation(conn, "import")
        conn.execute(
            insert(import_batch).values(
                operation_id=op_id,
                file_name=path.name,
                file_path=str(path.resolve()),
                md5=md5,
                file_size=size,
                id_validation=options.policy.describe(),
            )
        )
        return op_id

    op_id: int | None = None
    if not options.dry_run:
        with engine.begin() as conn:
            op_id = create_operation(conn)

    conn = engine.connect()
    trans = conn.begin()
    try:
        acquire_write_lock(conn, options.wait)
        if options.dry_run:
            op_id = create_operation(conn)
        assert op_id is not None
        duplicate = conn.execute(
            select(operation.c.id)
            .join(import_batch, import_batch.c.operation_id == operation.c.id)
            .where(
                import_batch.c.md5 == md5,
                operation.c.kind == "import",
                operation.c.status.in_(("completed", "partially_undone")),
                operation.c.id != op_id,
            )
        ).first()
        if duplicate is not None and not options.force:
            raise DuplicateFileError(
                f"a file with MD5 {md5} was already imported (operation {duplicate.id}); "
                "use --force to import it again"
            )
        records = load_records(path)
        valid, invalid = _validate_all(conn, records, options)
        importer = _Importer(conn, op_id, options)
        importer.counters["total_records"] = len(records)
        for index, rejection, seller_name in sorted(invalid, key=lambda item: item[0]):
            importer.rejections.append((index, rejection))
            importer.counters["invalid_records"] += 1
            if seller_name:
                importer.per_seller[seller_name]["invalid_records"] += 1
            log.warning(
                "record %d rejected (seller=%s): %s", index, safe_repr(seller_name), rejection
            )
        for index, rec in valid:
            importer.process(index, rec)

        counters = importer.counters
        conn.execute(
            update(import_batch)
            .where(import_batch.c.operation_id == op_id)
            .values(**{name: counters[name] for name in COUNTERS})
        )
        summary = {
            "per_seller": {name: dict(c) for name, c in sorted(importer.per_seller.items())},
            "rejections": [
                {"index": i, "code": r.code, "field": r.field} for i, r in importer.rejections
            ][:500],
        }
        finish_operation(conn, op_id, "completed", summary=summary)
        result = ImportResult(
            operation_id=None if options.dry_run else op_id,
            md5=md5,
            file_size=size,
            counters=counters,
            per_seller=dict(importer.per_seller),
            rejections=importer.rejections,
            dry_run=options.dry_run,
        )
        if options.dry_run:
            trans.rollback()
        else:
            trans.commit()
        return result
    except BaseException as exc:
        if trans.is_active:
            trans.rollback()
        if op_id is not None and not options.dry_run:
            _record_failure(engine, op_id, f"{type(exc).__name__}: {exc}")
        raise
    finally:
        conn.close()
