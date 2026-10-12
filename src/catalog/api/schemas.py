"""Response models of the REST API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class Ref(BaseModel):
    id: int
    name: str


class Brand(BaseModel):
    id: int
    name: str
    active: bool
    merged_into_id: int | None
    product_count: int


class Category(Brand):
    pass


class Seller(BaseModel):
    id: int
    name: str
    active: bool
    offer_count: int


class SellerCount(BaseModel):
    id: int
    name: str
    product_count: int


class ProductSummary(BaseModel):
    id: int
    uuid: uuid.UUID
    name: str
    active: bool
    merged_into_id: int | None
    brand: Ref | None
    categories: list[Ref]
    sellers: list[Ref]
    created: datetime


class Offer(BaseModel):
    id: int
    seller: Ref
    seller_product_id: uuid.UUID
    name: str
    brand_name: str | None
    category_name: str | None
    active: bool
    created: datetime
    updated: datetime


class ProductDetail(ProductSummary):
    offers: list[Offer]


class SellerOffer(BaseModel):
    id: int
    seller_product_id: uuid.UUID
    name: str
    brand_name: str | None
    category_name: str | None
    active: bool
    product: Ref
    product_uuid: uuid.UUID
    brand: Ref | None


class ImportSummary(BaseModel):
    id: int
    status: str
    file_name: str
    md5: str
    file_size: int
    id_validation: str
    started_at: datetime
    finished_at: datetime | None
    error_message: str | None
    total_records: int
    invalid_records: int
    skipped_records: int
    sellers_created: int
    products_created: int
    products_updated: int
    duplicate_products: int
    offers_created: int
    offers_updated: int
    unchanged: int


class ImportDetail(ImportSummary):
    per_seller: dict[str, dict[str, int]]
    rejections: list[dict[str, Any]]
    undos: list[int]


class MergeEntry(BaseModel):
    operation_id: int
    kind: str
    status: str
    entity_type: str
    kept_id: int
    merged_id: int | None
    merged_name: str | None
    origin: str
    created: datetime


class UndoEntry(BaseModel):
    operation_id: int
    status: str
    undo_kind: str
    criteria: dict[str, Any]
    changes_reverted: int
    conflicts_skipped: int
    targets: list[int]
    created: datetime


class Stats(BaseModel):
    products: int
    brands: int
    categories: int
    sellers: int
    offers: int
    last_import: ImportSummary | None


class Health(BaseModel):
    status: str
    database: bool
    schema_revision: str | None
    schema_head: str
    legacy_loaded: bool
