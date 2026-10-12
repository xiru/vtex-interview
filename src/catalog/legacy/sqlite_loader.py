"""One-time load of the legacy SQLite catalog into PostgreSQL.

Called by the ``0002_initial_data`` migration (``make psqlmigrate``). The SQLite file is opened
read-only and never modified. Rows are validated with the same rules as imports, copied into a
temporary staging table and then transformed with set-based SQL that uses the database key
function ``catalog.name_key`` — the same normalization every other code path uses.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, text

from catalog.config import HOUSE_SELLER_ID
from catalog.validation import check_text_fields
from catalog.validation.text import display_clean, optional_clean

log = logging.getLogger("catalog.legacy")

#: Namespace for deterministic UUIDs of legacy integer SellerProductId values.
LEGACY_SELLER_PRODUCT_NAMESPACE = uuid.UUID("5b1f3f0e-6c1d-4f43-9a55-3d1c0f6a4e10")

EXPECTED_COLUMNS = {
    "Product": {"Id", "Name", "Brand", "Category"},
    "SellerProduct": {"Id", "SellerName", "ProductId", "SellerProductId"},
}


class LegacyLoadError(RuntimeError):
    """The legacy file is unusable or failed validation/verification; nothing was loaded."""


@dataclass(frozen=True)
class LegacyProduct:
    id: int
    name: str
    brand: str | None
    category: str | None


@dataclass(frozen=True)
class LegacyOffer:
    seller_name: str
    product_id: int
    seller_product_id: uuid.UUID


def read_legacy(path: str | Path) -> tuple[list[LegacyProduct], list[LegacyOffer]]:
    """Read and validate the legacy SQLite file (opened read-only)."""
    uri = f"file:{Path(path).resolve()}?mode=ro"
    try:
        sqlite = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:  # pragma: no cover - depends on filesystem errors
        raise LegacyLoadError(f"cannot open legacy SQLite file {path}: {exc}") from exc
    try:
        for table, columns in EXPECTED_COLUMNS.items():
            found = {row[1] for row in sqlite.execute(f"PRAGMA table_info('{table}')")}
            if not columns <= found:
                raise LegacyLoadError(
                    f"legacy table {table} is missing columns {sorted(columns - found)}"
                )
        products_raw = sqlite.execute(
            "SELECT Id, Name, Brand, Category FROM Product ORDER BY Id"
        ).fetchall()
        offers_raw = sqlite.execute(
            "SELECT SellerName, ProductId, SellerProductId FROM SellerProduct ORDER BY Id"
        ).fetchall()
    except sqlite3.Error as exc:
        raise LegacyLoadError(f"cannot read legacy SQLite file {path}: {exc}") from exc
    finally:
        sqlite.close()

    errors: list[str] = []
    products: list[LegacyProduct] = []
    for pid, name, brand, category in products_raw:
        values = {"Name": name, "Brand": brand, "Category": category}
        if not isinstance(name, str) or not display_clean(name):
            errors.append(f"Product {pid}: empty name")
            continue
        rejection = check_text_fields(values)
        if rejection:
            errors.append(f"Product {pid}: {rejection}")
            continue
        products.append(
            LegacyProduct(
                pid, display_clean(name), optional_clean(brand), optional_clean(category)
            )
        )
    offers: list[LegacyOffer] = []
    for seller_name, product_id, seller_product_id in offers_raw:
        rejection = check_text_fields({"SellerName": seller_name})
        if rejection or not display_clean(seller_name or ""):
            errors.append(f"SellerProduct {seller_name!r}/{product_id}: {rejection or 'empty'}")
            continue
        offers.append(
            LegacyOffer(
                display_clean(seller_name),
                int(product_id),
                uuid.uuid5(LEGACY_SELLER_PRODUCT_NAMESPACE, f"{seller_name}:{seller_product_id}"),
            )
        )
    if errors:
        raise LegacyLoadError(
            "legacy data failed validation; fix these rows and run again:\n  "
            + "\n  ".join(errors[:50])
        )
    return products, offers


def _scalar(conn: Connection, sql: str, **params: Any) -> Any:
    return conn.execute(text(sql), params).scalar()


def load_legacy(conn: Connection, path: str | Path) -> dict[str, Any]:
    """Load the legacy catalog into an empty schema; returns the verification summary."""
    products, offers = read_legacy(path)
    if _scalar(conn, "SELECT count(*) FROM catalog.product"):
        raise LegacyLoadError(
            "the catalog already contains products; the legacy load needs an empty catalog"
        )

    op_id = _scalar(
        conn,
        "INSERT INTO catalog.operation (kind, status) VALUES ('legacy_load', 'running') "
        "RETURNING id",
    )

    conn.execute(
        text(
            "CREATE TEMP TABLE legacy_product (id bigint PRIMARY KEY, name text NOT NULL, "
            "brand text, category text) ON COMMIT DROP"
        )
    )
    conn.execute(
        text(
            "CREATE TEMP TABLE legacy_offer (seller_name text, product_id bigint, "
            "seller_product_id uuid) ON COMMIT DROP"
        )
    )
    if products:
        conn.execute(
            text("INSERT INTO legacy_product VALUES (:id, :name, :brand, :category)"),
            [p.__dict__ for p in products],
        )
    if offers:
        conn.execute(
            text(
                "INSERT INTO legacy_offer VALUES (:seller_name, :product_id, :seller_product_id)"
            ),
            [o.__dict__ for o in offers],
        )

    empty = (
        conn.execute(
            text(
                "SELECT v FROM (SELECT name AS v FROM legacy_product"
                " UNION SELECT brand FROM legacy_product"
                " UNION SELECT category FROM legacy_product"
                " UNION SELECT seller_name FROM legacy_offer) s"
                " WHERE v IS NOT NULL AND catalog.name_key(v) = ''"
            )
        )
        .scalars()
        .all()
    )
    if empty:
        raise LegacyLoadError(f"names without latin letters/digits (EMPTY_KEY): {empty[:20]}")

    # Brands and categories: one row per key, displayed with the most used spelling. Other
    # spellings of the same key are recorded as legacy-load merges for auditability.
    for entity, column, display in (
        ("brand", "brand", "spelling"),
        ("category", "category", "lower(spelling)"),
    ):
        conn.execute(
            text(
                f"""
                WITH spelled AS (
                    SELECT {column} AS spelling, count(*) AS uses, min(id) AS first_id
                    FROM legacy_product WHERE {column} IS NOT NULL GROUP BY {column}
                ), ranked AS (
                    SELECT spelling, row_number() OVER (
                        PARTITION BY catalog.name_key(spelling) ORDER BY uses DESC, first_id
                    ) AS rn
                    FROM spelled
                )
                INSERT INTO catalog.{entity} (name)
                SELECT {display} FROM ranked WHERE rn = 1 ORDER BY 1
                """
            )
        )
        conn.execute(
            text(
                f"""
                INSERT INTO catalog.merge_detail
                    (operation_id, entity_type, kept_id, merged_name, origin)
                SELECT :op, '{entity}', e.id, s.spelling, 'legacy_load'
                FROM (SELECT DISTINCT {column} AS spelling FROM legacy_product
                      WHERE {column} IS NOT NULL) s
                JOIN catalog.{entity} e ON catalog.name_key(e.name) = catalog.name_key(s.spelling)
                WHERE e.name <> {display.replace("spelling", "s.spelling")}
                ORDER BY e.id, s.spelling
                """
            ),
            {"op": op_id},
        )

    # Products keep their legacy ids; uuidv7() is evaluated in id order.
    conn.execute(
        text(
            """
            INSERT INTO catalog.product (id, name, brand_id)
            SELECT lp.id, lp.name, b.id
            FROM legacy_product lp
            LEFT JOIN catalog.brand b ON catalog.name_key(b.name) = catalog.name_key(lp.brand)
            ORDER BY lp.id
            """
        )
    )
    conn.execute(
        text(
            """
            INSERT INTO catalog.product_category (product_id, category_id)
            SELECT lp.id, c.id
            FROM legacy_product lp
            JOIN catalog.category c ON catalog.name_key(c.name) = catalog.name_key(lp.category)
            """
        )
    )

    # Legacy seller links (the shipped file has none), then the house seller's offers.
    conn.execute(
        text(
            "INSERT INTO catalog.seller (name) SELECT DISTINCT ON (catalog.name_key(seller_name)) "
            "seller_name FROM legacy_offer ORDER BY catalog.name_key(seller_name), seller_name "
            "ON CONFLICT (catalog.name_key(name)) DO NOTHING"
        )
    )
    legacy_links = conn.execute(
        text(
            """
            INSERT INTO catalog.seller_product
                (seller_id, product_id, seller_product_id, name, brand_name, category_name,
                 last_operation_id)
            SELECT s.id, p.id, lo.seller_product_id, lp.name, lp.brand, lp.category, :op
            FROM legacy_offer lo
            JOIN catalog.seller s ON catalog.name_key(s.name) = catalog.name_key(lo.seller_name)
            JOIN legacy_product lp ON lp.id = lo.product_id
            JOIN catalog.product p ON p.id = lp.id
            ON CONFLICT DO NOTHING
            """
        ),
        {"op": op_id},
    ).rowcount
    conn.execute(
        text(
            """
            INSERT INTO catalog.seller_product
                (seller_id, product_id, seller_product_id, name, brand_name, category_name,
                 last_operation_id)
            SELECT :house, p.id, p.uuid, lp.name, lp.brand, lp.category, :op
            FROM catalog.product p JOIN legacy_product lp ON lp.id = p.id
            ORDER BY p.id
            ON CONFLICT DO NOTHING
            """
        ),
        {"house": HOUSE_SELLER_ID, "op": op_id},
    )

    for table in ("seller", "brand", "category", "product", "seller_product"):
        conn.execute(
            text(
                f"SELECT setval(pg_get_serial_sequence('catalog.{table}', 'id'), "
                f"greatest((SELECT max(id) FROM catalog.{table}), 1))"
            )
        )

    summary = {
        "source": str(Path(path).name),
        "legacy_products": len(products),
        "legacy_seller_links": len(offers),
        "products": _scalar(conn, "SELECT count(*) FROM catalog.product"),
        "brands": _scalar(conn, "SELECT count(*) FROM catalog.brand"),
        "brand_spellings": _scalar(
            conn, "SELECT count(DISTINCT brand) FROM legacy_product WHERE brand IS NOT NULL"
        ),
        "categories": _scalar(conn, "SELECT count(*) FROM catalog.category"),
        "product_categories": _scalar(conn, "SELECT count(*) FROM catalog.product_category"),
        "sellers": _scalar(conn, "SELECT count(*) FROM catalog.seller"),
        "house_offers": _scalar(
            conn,
            "SELECT count(*) FROM catalog.seller_product WHERE seller_id = :h",
            h=HOUSE_SELLER_ID,
        ),
        "seller_links_loaded": legacy_links,
        "merges": _scalar(
            conn, "SELECT count(*) FROM catalog.merge_detail WHERE operation_id = :op", op=op_id
        ),
    }
    expected = {
        "products": len(products),
        "brands": _scalar(
            conn,
            "SELECT count(DISTINCT catalog.name_key(brand)) FROM legacy_product "
            "WHERE brand IS NOT NULL",
        ),
        "categories": _scalar(
            conn,
            "SELECT count(DISTINCT catalog.name_key(category)) FROM legacy_product "
            "WHERE category IS NOT NULL",
        ),
        "product_categories": sum(1 for p in products if p.category),
    }
    mismatches = {k: (summary[k], v) for k, v in expected.items() if summary[k] != v}
    if mismatches:
        raise LegacyLoadError(f"verification failed (loaded, expected): {mismatches}")
    summary["verified"] = True

    conn.execute(
        text(
            "UPDATE catalog.operation SET status = 'completed', finished_at = now(), "
            "summary = CAST(:summary AS jsonb) WHERE id = :op"
        ),
        {"summary": json.dumps(summary), "op": op_id},
    )
    log.info("legacy load completed: %s", summary)
    return summary
