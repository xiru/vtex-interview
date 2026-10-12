"""Plain helpers shared by the tests (records, legacy SQLite files, snapshots)."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, text

DATA = Path(__file__).resolve().parent.parent / "data"


def make_record(
    name: str,
    seller: str = "MegaStore",
    brand: str | None = None,
    category: str | None = None,
    record_id: str | None = None,
) -> dict[str, Any]:
    return {
        "Id": record_id or str(uuid.uuid4()),
        "SellerName": seller,
        "Name": name,
        "Brand": brand,
        "Category": category,
    }


def make_legacy_sqlite(
    path: Path,
    products: list[tuple[int, str, str | None, str | None]],
    offers: list[tuple[str, int, int]] = (),
) -> Path:
    """Create a SQLite file with the legacy schema."""
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE Product (Id INTEGER PRIMARY KEY AUTOINCREMENT, Name TEXT NOT NULL, "
        "Brand TEXT, Category TEXT)"
    )
    conn.execute(
        "CREATE TABLE SellerProduct (Id INTEGER PRIMARY KEY AUTOINCREMENT, SellerName TEXT NOT "
        "NULL, ProductId INTEGER NOT NULL, SellerProductId INTEGER NOT NULL)"
    )
    conn.executemany("INSERT INTO Product VALUES (?, ?, ?, ?)", products)
    conn.executemany(
        "INSERT INTO SellerProduct (SellerName, ProductId, SellerProductId) VALUES (?, ?, ?)",
        offers,
    )
    conn.commit()
    conn.close()
    return path


SEED_PRODUCTS = [
    (1, "iPhone 15 Pro", "Apple", "Electronics"),
    (2, "MacBook Air M2", "apple", "Computers"),
    (3, "Smartphone Galaxy S23", "Samsung", "Electronics"),
    (4, "Cable Organizer Kit", None, "Accessories"),
    (5, "Knife Set Professional", "Wusthof", None),
    (6, "Mouse Logitech MX Master", "Logitech", "Accessories"),
]


CORE_COLUMNS = {
    "seller": "id, name, active",
    "brand": "id, name, active, merged_into_id",
    "category": "id, name, active, merged_into_id",
    "product": "id, uuid, name, brand_id, active, merged_into_id",
    "product_category": "product_id, category_id, active",
    "seller_product": (
        "id, seller_id, product_id, seller_product_id, name, brand_name, category_name, active"
    ),
}


def snapshot(engine: Engine) -> dict[str, list[tuple]]:
    """Every catalog row (business columns only), for whole-state comparisons."""
    with engine.connect() as conn:
        return {
            table: sorted(
                tuple(row) for row in conn.execute(text(f"SELECT {cols} FROM catalog.{table}"))
            )
            for table, cols in CORE_COLUMNS.items()
        }


def snapshot_active(engine: Engine) -> dict[str, list[tuple]]:
    """Only the active rows: what a catalog consumer sees."""
    with engine.connect() as conn:
        return {
            table: sorted(
                tuple(row)
                for row in conn.execute(text(f"SELECT {cols} FROM catalog.{table} WHERE active"))
            )
            for table, cols in CORE_COLUMNS.items()
        }


def scalar(engine: Engine, sql: str, **params: Any) -> Any:
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar()


def fetch_all(engine: Engine, sql: str, **params: Any) -> list[Any]:
    with engine.connect() as conn:
        return list(conn.execute(text(sql), params).all())
