"""Catalog endpoints: brands, categories, sellers and products (GET only)."""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import ColumnElement, Connection, Select, and_, exists, func, select

from catalog.api.deps import Conn, Pages, Paging, ShowInactive
from catalog.api.schemas import (
    Brand,
    Category,
    Offer,
    Page,
    ProductDetail,
    ProductSummary,
    Ref,
    Seller,
    SellerCount,
    SellerOffer,
)
from catalog.models import brand, category, product, product_category, seller, seller_product

router = APIRouter()


def key_equals(column, value: str):
    """Name lookup through the database key function (uses the unique key indexes)."""
    return func.catalog.name_key(column) == func.catalog.name_key(value)


def contains(column, value: str):
    """Accent/case/punctuation-insensitive substring search (trigram index)."""
    # name_fold() only yields [a-z0-9 ], so the pattern cannot contain LIKE wildcards.
    return func.catalog.name_fold(column).contains(func.catalog.name_fold(value))


def paginate(conn: Connection, query: Select, page: Paging, order_by) -> tuple[list[Any], int]:
    total = conn.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    rows = conn.execute(query.order_by(order_by).limit(page.limit).offset(page.offset)).all()
    return list(rows), int(total or 0)


def not_found(what: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{what} not found")


# --------------------------------------------------------------------------- brands
def _named_list(conn, table, page, show_inactive, q, count_query):
    query = select(table, count_query.label("product_count"))
    if not show_inactive:
        query = query.where(table.c.active)
    if q:
        query = query.where(contains(table.c.name, q))
    return paginate(conn, query, page, table.c.id)


def _brand_product_count():
    return (
        select(func.count())
        .where(product.c.brand_id == brand.c.id, product.c.active)
        .correlate(brand)
        .scalar_subquery()
    )


def _category_product_count():
    return (
        select(func.count())
        .select_from(product_category.join(product, product.c.id == product_category.c.product_id))
        .where(
            product_category.c.category_id == category.c.id,
            product_category.c.active,
            product.c.active,
        )
        .correlate(category)
        .scalar_subquery()
    )


def _get_named(conn, table, row_id, show_inactive, count_query, what):
    row = conn.execute(
        select(table, count_query.label("product_count")).where(table.c.id == row_id)
    ).first()
    if row is None or (not row.active and not show_inactive):
        raise not_found(what)
    return row


@router.get("/brands", response_model=Page[Brand], tags=["brands"])
def list_brands(
    conn: Connection = Conn,
    page: Paging = Pages,
    show_inactive: bool = ShowInactive,
    q: str | None = Query(None, description="name contains (accent/case-insensitive)"),
):
    rows, total = _named_list(conn, brand, page, show_inactive, q, _brand_product_count())
    return Page[Brand](
        items=[Brand.model_validate(r._mapping) for r in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/brands/{brand_id}", response_model=Brand, tags=["brands"])
def get_brand(brand_id: int, conn: Connection = Conn, show_inactive: bool = ShowInactive):
    row = _get_named(conn, brand, brand_id, show_inactive, _brand_product_count(), "brand")
    return Brand.model_validate(row._mapping)


def _sellers_of(conn, page, product_filter) -> Page[SellerCount]:
    query = (
        select(
            seller.c.id,
            seller.c.name,
            func.count(func.distinct(product.c.id)).label("product_count"),
        )
        .select_from(
            seller_product.join(seller, seller.c.id == seller_product.c.seller_id).join(
                product, product.c.id == seller_product.c.product_id
            )
        )
        .where(seller_product.c.active, product.c.active, seller.c.active, product_filter)
        .group_by(seller.c.id, seller.c.name)
    )
    rows, total = paginate(conn, query, page, seller.c.id)
    return Page[SellerCount](
        items=[SellerCount.model_validate(r._mapping) for r in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/brands/{brand_id}/sellers", response_model=Page[SellerCount], tags=["brands"])
def brand_sellers(
    brand_id: int,
    conn: Connection = Conn,
    page: Paging = Pages,
    show_inactive: bool = ShowInactive,
):
    _get_named(conn, brand, brand_id, show_inactive, _brand_product_count(), "brand")
    return _sellers_of(conn, page, product.c.brand_id == brand_id)


# --------------------------------------------------------------------------- categories
@router.get("/categories", response_model=Page[Category], tags=["categories"])
def list_categories(
    conn: Connection = Conn,
    page: Paging = Pages,
    show_inactive: bool = ShowInactive,
    q: str | None = Query(None, description="name contains (accent/case-insensitive)"),
):
    rows, total = _named_list(conn, category, page, show_inactive, q, _category_product_count())
    return Page[Category](
        items=[Category.model_validate(r._mapping) for r in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/categories/{category_id}", response_model=Category, tags=["categories"])
def get_category(category_id: int, conn: Connection = Conn, show_inactive: bool = ShowInactive):
    row = _get_named(
        conn, category, category_id, show_inactive, _category_product_count(), "category"
    )
    return Category.model_validate(row._mapping)


@router.get(
    "/categories/{category_id}/sellers", response_model=Page[SellerCount], tags=["categories"]
)
def category_sellers(
    category_id: int,
    conn: Connection = Conn,
    page: Paging = Pages,
    show_inactive: bool = ShowInactive,
):
    _get_named(conn, category, category_id, show_inactive, _category_product_count(), "category")
    linked = exists().where(
        product_category.c.product_id == product.c.id,
        product_category.c.category_id == category_id,
        product_category.c.active,
    )
    return _sellers_of(conn, page, linked)


# --------------------------------------------------------------------------- sellers
def _offer_count():
    return (
        select(func.count())
        .where(seller_product.c.seller_id == seller.c.id, seller_product.c.active)
        .correlate(seller)
        .scalar_subquery()
    )


def _seller_by_name(conn: Connection, name: str, show_inactive: bool):
    row = conn.execute(
        select(seller, _offer_count().label("offer_count")).where(key_equals(seller.c.name, name))
    ).first()
    if row is None or (not row.active and not show_inactive):
        raise not_found("seller")
    return row


@router.get("/sellers", response_model=Page[Seller], tags=["sellers"])
def list_sellers(
    conn: Connection = Conn,
    page: Paging = Pages,
    show_inactive: bool = ShowInactive,
    q: str | None = Query(None, description="name contains"),
):
    query = select(seller, _offer_count().label("offer_count"))
    if not show_inactive:
        query = query.where(seller.c.active)
    if q:
        query = query.where(contains(seller.c.name, q))
    rows, total = paginate(conn, query, page, seller.c.id)
    return Page[Seller](
        items=[Seller.model_validate(r._mapping) for r in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/sellers/{name}", response_model=Seller, tags=["sellers"])
def get_seller(name: str, conn: Connection = Conn, show_inactive: bool = ShowInactive):
    return Seller.model_validate(_seller_by_name(conn, name, show_inactive)._mapping)


@router.get("/sellers/{name}/products", response_model=Page[SellerOffer], tags=["sellers"])
def seller_products(
    name: str, conn: Connection = Conn, page: Paging = Pages, show_inactive: bool = ShowInactive
):
    found = _seller_by_name(conn, name, show_inactive)
    query = (
        select(
            seller_product,
            product.c.name.label("product_name"),
            product.c.uuid.label("product_uuid"),
            brand.c.id.label("brand_id"),
            brand.c.name.label("brand_ref_name"),
        )
        .select_from(
            seller_product.join(product, product.c.id == seller_product.c.product_id).outerjoin(
                brand, brand.c.id == product.c.brand_id
            )
        )
        .where(seller_product.c.seller_id == found.id)
    )
    if not show_inactive:
        query = query.where(seller_product.c.active)
    rows, total = paginate(conn, query, page, seller_product.c.id)
    items = [
        SellerOffer(
            id=r.id,
            seller_product_id=r.seller_product_id,
            name=r.name,
            brand_name=r.brand_name,
            category_name=r.category_name,
            active=r.active,
            product=Ref(id=r.product_id, name=r.product_name),
            product_uuid=r.product_uuid,
            brand=Ref(id=r.brand_id, name=r.brand_ref_name) if r.brand_id else None,
        )
        for r in rows
    ]
    return Page[SellerOffer](items=items, total=total, limit=page.limit, offset=page.offset)


# --------------------------------------------------------------------------- products
def _product_query():
    return select(product, brand.c.name.label("brand_name")).select_from(
        product.outerjoin(brand, brand.c.id == product.c.brand_id)
    )


def _embed(conn: Connection, rows: list[Any], show_inactive: bool) -> list[ProductSummary]:
    """Attach categories and sellers with one query each (no N+1)."""
    ids = [r.id for r in rows]
    categories: dict[int, list[Ref]] = defaultdict(list)
    sellers: dict[int, list[Ref]] = defaultdict(list)
    if ids:
        cat_query = (
            select(product_category.c.product_id, category.c.id, category.c.name)
            .join(category, category.c.id == product_category.c.category_id)
            .where(product_category.c.product_id.in_(ids))
            .order_by(category.c.name)
        )
        if not show_inactive:
            cat_query = cat_query.where(product_category.c.active, category.c.active)
        for r in conn.execute(cat_query):
            categories[r.product_id].append(Ref(id=r.id, name=r.name))
        seller_query = (
            select(seller_product.c.product_id, seller.c.id, seller.c.name)
            .join(seller, seller.c.id == seller_product.c.seller_id)
            .where(seller_product.c.product_id.in_(ids))
            .order_by(seller.c.name)
        )
        if not show_inactive:
            seller_query = seller_query.where(seller_product.c.active, seller.c.active)
        for r in conn.execute(seller_query):
            sellers[r.product_id].append(Ref(id=r.id, name=r.name))
    return [
        ProductSummary(
            id=r.id,
            uuid=r.uuid,
            name=r.name,
            active=r.active,
            merged_into_id=r.merged_into_id,
            brand=Ref(id=r.brand_id, name=r.brand_name) if r.brand_id else None,
            categories=categories[r.id],
            sellers=sellers[r.id],
            created=r.created,
        )
        for r in rows
    ]


@router.get("/products", response_model=Page[ProductSummary], tags=["products"])
def list_products(
    conn: Connection = Conn,
    page: Paging = Pages,
    show_inactive: bool = ShowInactive,
    brand_name: str | None = Query(None, alias="brand", description="brand name (any spelling)"),
    category_name: str | None = Query(None, alias="category", description="category name"),
    seller_name: str | None = Query(None, alias="seller", description="seller name"),
    brand_id: int | None = Query(None),
    category_id: int | None = Query(None),
    q: str | None = Query(None, description="product name contains"),
):
    query = _product_query()
    conditions: list[ColumnElement[bool]] = []
    if not show_inactive:
        conditions.append(product.c.active)
    if brand_id is not None:
        conditions.append(product.c.brand_id == brand_id)
    if brand_name:
        conditions.append(key_equals(brand.c.name, brand_name))
    if category_id is not None or category_name:
        link = (
            select(product_category.c.product_id)
            .join(category, category.c.id == product_category.c.category_id)
            .where(product_category.c.product_id == product.c.id, product_category.c.active)
        )
        if category_id is not None:
            link = link.where(category.c.id == category_id)
        if category_name:
            link = link.where(key_equals(category.c.name, category_name))
        conditions.append(link.exists())
    if seller_name:
        offer = (
            select(seller_product.c.id)
            .join(seller, seller.c.id == seller_product.c.seller_id)
            .where(
                seller_product.c.product_id == product.c.id,
                seller_product.c.active,
                key_equals(seller.c.name, seller_name),
            )
        )
        conditions.append(offer.exists())
    if q:
        conditions.append(contains(product.c.name, q))
    if conditions:
        query = query.where(and_(*conditions))
    rows, total = paginate(conn, query, page, product.c.id)
    return Page[ProductSummary](
        items=_embed(conn, rows, show_inactive), total=total, limit=page.limit, offset=page.offset
    )


def _product_detail(conn: Connection, row, show_inactive: bool) -> ProductDetail:
    if row is None or (not row.active and not show_inactive):
        raise not_found("product")
    summary = _embed(conn, [row], show_inactive)[0]
    offer_query = (
        select(seller_product, seller.c.name.label("seller_name"))
        .join(seller, seller.c.id == seller_product.c.seller_id)
        .where(seller_product.c.product_id == row.id)
        .order_by(seller.c.name)
    )
    if not show_inactive:
        offer_query = offer_query.where(seller_product.c.active)
    offers = [
        Offer(
            id=o.id,
            seller=Ref(id=o.seller_id, name=o.seller_name),
            seller_product_id=o.seller_product_id,
            name=o.name,
            brand_name=o.brand_name,
            category_name=o.category_name,
            active=o.active,
            created=o.created,
            updated=o.updated,
        )
        for o in conn.execute(offer_query)
    ]
    return ProductDetail(**summary.model_dump(), offers=offers)


@router.get("/products/uuid/{product_uuid}", response_model=ProductDetail, tags=["products"])
def get_product_by_uuid(
    product_uuid: uuid.UUID, conn: Connection = Conn, show_inactive: bool = ShowInactive
):
    row = conn.execute(_product_query().where(product.c.uuid == product_uuid)).first()
    return _product_detail(conn, row, show_inactive)


@router.get("/products/{product_id}", response_model=ProductDetail, tags=["products"])
def get_product(product_id: int, conn: Connection = Conn, show_inactive: bool = ShowInactive):
    row = conn.execute(_product_query().where(product.c.id == product_id)).first()
    return _product_detail(conn, row, show_inactive)
