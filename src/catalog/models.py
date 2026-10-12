"""SQLAlchemy Core table definitions for the ``catalog`` schema.

The authoritative DDL (functions, expression indexes, CHECKs, triggers) lives in the Alembic
migration ``0001_schema``; these definitions mirror the columns so queries can be written with
SQLAlchemy Core instead of string-built SQL.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

SCHEMA = "catalog"
metadata = MetaData(schema=SCHEMA)


def _timestamps() -> list[Column]:
    return [
        Column("created", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ]


seller = Table(
    "seller",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("name", Text, nullable=False),
    Column("active", Boolean, nullable=False, server_default="true"),
    *_timestamps(),
)

brand = Table(
    "brand",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("name", Text, nullable=False),
    Column("active", Boolean, nullable=False, server_default="true"),
    Column("merged_into_id", BigInteger),
    *_timestamps(),
)

category = Table(
    "category",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("name", Text, nullable=False),
    Column("active", Boolean, nullable=False, server_default="true"),
    Column("merged_into_id", BigInteger),
    *_timestamps(),
)

product = Table(
    "product",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("uuid", Uuid, nullable=False),
    Column("name", Text, nullable=False),
    Column("brand_id", BigInteger),
    Column("active", Boolean, nullable=False, server_default="true"),
    Column("merged_into_id", BigInteger),
    *_timestamps(),
)

product_category = Table(
    "product_category",
    metadata,
    Column("product_id", BigInteger, primary_key=True),
    Column("category_id", BigInteger, primary_key=True),
    Column("active", Boolean, nullable=False, server_default="true"),
    *_timestamps(),
)

seller_product = Table(
    "seller_product",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("seller_id", BigInteger, nullable=False),
    Column("product_id", BigInteger, nullable=False),
    Column("seller_product_id", Uuid, nullable=False),
    Column("name", Text, nullable=False),
    Column("brand_name", Text),
    Column("category_name", Text),
    Column("active", Boolean, nullable=False, server_default="true"),
    Column("last_operation_id", BigInteger),
    *_timestamps(),
)

operation = Table(
    "operation",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("kind", String(20), nullable=False),
    Column("status", String(20), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("finished_at", DateTime(timezone=True)),
    Column("error_message", Text),
    Column("summary", JSONB),
    *_timestamps(),
)

import_batch = Table(
    "import_batch",
    metadata,
    Column("operation_id", BigInteger, primary_key=True),
    Column("file_name", Text, nullable=False),
    Column("file_path", Text),
    Column("md5", String(32), nullable=False),
    Column("file_size", BigInteger, nullable=False),
    Column("id_validation", Text, nullable=False),
    Column("total_records", Integer, nullable=False, server_default="0"),
    Column("invalid_records", Integer, nullable=False, server_default="0"),
    Column("skipped_records", Integer, nullable=False, server_default="0"),
    Column("sellers_created", Integer, nullable=False, server_default="0"),
    Column("products_created", Integer, nullable=False, server_default="0"),
    Column("products_updated", Integer, nullable=False, server_default="0"),
    Column("duplicate_products", Integer, nullable=False, server_default="0"),
    Column("offers_created", Integer, nullable=False, server_default="0"),
    Column("offers_updated", Integer, nullable=False, server_default="0"),
    Column("unchanged", Integer, nullable=False, server_default="0"),
    *_timestamps(),
)

merge_detail = Table(
    "merge_detail",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("operation_id", BigInteger, nullable=False),
    Column("entity_type", String(20), nullable=False),
    Column("kept_id", BigInteger, nullable=False),
    Column("merged_id", BigInteger),
    Column("merged_name", Text),
    Column("origin", String(20), nullable=False),
    *_timestamps(),
)

undo_detail = Table(
    "undo_detail",
    metadata,
    Column("operation_id", BigInteger, primary_key=True),
    Column("undo_kind", String(20), nullable=False),
    Column("criteria", JSONB, nullable=False),
    Column("changes_reverted", Integer, nullable=False, server_default="0"),
    Column("conflicts_skipped", Integer, nullable=False, server_default="0"),
    *_timestamps(),
)

change_log = Table(
    "change_log",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("operation_id", BigInteger, nullable=False),
    Column("seller_id", BigInteger),
    Column("table_name", String(40), nullable=False),
    Column("row_pk", JSONB, nullable=False),
    Column("action", String(10), nullable=False),
    Column("old_values", JSONB),
    Column("new_values", JSONB, nullable=False),
    Column("reverts_change_id", BigInteger),
    Column("reverted_by_operation_id", BigInteger),
    *_timestamps(),
)

#: Tables whose rows are tracked by the change log (soft-deactivated, never deleted).
TRACKED_TABLES = {
    t.name: t for t in (seller, brand, category, product, product_category, seller_product)
}
