"""REST API (read-only, catalog_reader role)."""

import pytest
from fastapi.testclient import TestClient

from catalog.api.app import create_app
from catalog.config import Settings
from catalog.services import merge, undo
from catalog.services.importer import run_import
from helpers import make_record, scalar

API = "/api/v1"


@pytest.fixture
def client(seeded, reader_engine, db_urls):
    settings = Settings(
        database_url=db_urls.owner,
        api_database_url=db_urls.reader,
        allow_empty_catalog=False,
        cors_origins=("http://ui.example",),
    )
    with TestClient(create_app(settings, engine=reader_engine)) as test_client:
        yield test_client


@pytest.fixture
def imported(seeded, write_json):
    return run_import(
        seeded,
        write_json(
            [
                make_record("iPhone 15 Pro", "MegaStore", "Apple", "Mobile Phones"),
                make_record("iPhone 15 Pro", "TechWorld", "apple", "Electronics"),
                make_record("Galaxy Tab S9", "TechWorld", "Samsung", "Tablets"),
                make_record("Black and Decker Drill", "ToolShop", "BLACK+DECKER", "Tools"),
            ]
        ),
    )


def get(client, path, expect=200, **params):
    response = client.get(API + path, params=params)
    assert response.status_code == expect, response.text
    return response.json()


def test_brands(client, imported):
    page = get(client, "/brands", q="apple")
    assert page["total"] == 1
    apple = page["items"][0]
    assert apple["name"] == "Apple" and apple["product_count"] == 2
    assert get(client, f"/brands/{apple['id']}")["name"] == "Apple"
    sellers = get(client, f"/brands/{apple['id']}/sellers")["items"]
    assert {s["name"]: s["product_count"] for s in sellers} == {
        "VTEX": 2,
        "MegaStore": 1,
        "TechWorld": 1,
    }
    get(client, "/brands/99999", expect=404)


def test_categories(client, imported):
    page = get(client, "/categories", limit=1000)
    names = [c["name"] for c in page["items"]]
    ids = [c["id"] for c in page["items"]]
    assert ids == sorted(ids)  # stable ordering for pagination
    assert "mobile phones" in names and all(n == n.lower() for n in names)
    electronics = next(c for c in page["items"] if c["name"] == "electronics")
    sellers = get(client, f"/categories/{electronics['id']}/sellers")["items"]
    assert {s["name"] for s in sellers} == {"VTEX", "TechWorld", "MegaStore"}
    assert get(client, f"/categories/{electronics['id']}")["product_count"] >= 2
    get(client, "/categories/99999/sellers", expect=404)


def test_sellers(client, imported):
    page = get(client, "/sellers")
    assert [s["name"] for s in page["items"]][:1] == ["VTEX"]
    tech = get(client, "/sellers/techworld")  # any spelling
    assert tech["name"] == "TechWorld" and tech["offer_count"] == 2
    offers = get(client, "/sellers/TechWorld/products")["items"]
    assert {o["product"]["name"] for o in offers} == {"iPhone 15 Pro", "Galaxy Tab S9"}
    assert all(o["brand"] for o in offers)
    get(client, "/sellers/nobody", expect=404)
    assert get(client, "/sellers", q="tech")["total"] == 1


def test_products_filters_and_embedding(client, imported):
    page = get(client, "/products", brand="APPLE")
    assert page["total"] == 2
    iphone = next(p for p in page["items"] if p["name"] == "iPhone 15 Pro")
    assert {s["name"] for s in iphone["sellers"]} == {"VTEX", "MegaStore", "TechWorld"}
    assert {c["name"] for c in iphone["categories"]} == {"electronics", "mobile phones"}
    assert get(client, "/products", brand="Black & Decker")["total"] == 1
    assert get(client, "/products", category="Mobile-Phones")["total"] == 1
    assert get(client, "/products", seller="megastore")["total"] == 1
    assert get(client, "/products", q="galaxy")["total"] == 2  # tab + legacy S23
    brand_id = iphone["brand"]["id"]
    assert get(client, "/products", brand_id=brand_id)["total"] == 2
    cat_id = next(c["id"] for c in iphone["categories"] if c["name"] == "electronics")
    assert get(client, "/products", category_id=cat_id, seller="TechWorld")["total"] == 1
    assert get(client, "/products", brand="unknown brand")["total"] == 0


def test_product_detail(client, imported):
    detail = get(client, "/products/1")
    assert detail["name"] == "iPhone 15 Pro"
    raw = {o["seller"]["name"]: o["brand_name"] for o in detail["offers"]}
    assert raw == {"VTEX": "Apple", "MegaStore": "Apple", "TechWorld": "apple"}
    assert get(client, f"/products/uuid/{detail['uuid']}")["id"] == 1
    get(client, "/products/99999", expect=404)
    get(client, "/products/uuid/00000000-0000-7000-8000-000000000000", expect=404)


def test_pagination_bounds(client, imported):
    page = get(client, "/products", limit=2, offset=1)
    assert len(page["items"]) == 2 and page["limit"] == 2 and page["offset"] == 1
    assert get(client, "/products")["limit"] == 100
    assert len(get(client, "/products", limit=1000)["items"]) == page["total"]
    for bad in ({"limit": 0}, {"limit": 1001}, {"offset": -1}):
        client_response = client.get(API + "/products", params=bad)
        assert client_response.status_code == 422


def test_inactive_rows_are_hidden_unless_requested(client, seeded, imported):
    with seeded.begin() as conn:
        merged = merge.merge_products(conn, 1, [2])
    assert merged.operation_id
    get(client, "/products/2", expect=404)
    shown = get(client, "/products/2", show_inactive=True)
    assert shown["active"] is False and shown["merged_into_id"] == 1
    assert (
        get(client, "/products", show_inactive=True)["total"]
        == get(client, "/products")["total"] + 1
    )


def test_imports_merges_undos_and_stats(client, seeded, imported):
    page = get(client, "/imports")
    assert page["total"] == 1
    item = page["items"][0]
    assert item["md5"] == imported.md5 and item["offers_created"] == 4
    assert get(client, "/imports", md5=imported.md5.upper())["total"] == 1
    assert get(client, "/imports", seller="toolshop")["total"] == 1
    assert get(client, "/imports", seller="nobody")["total"] == 0
    assert get(client, "/imports", status="failed")["total"] == 0
    assert (
        get(client, "/imports", since="2000-01-01T00:00:00Z", until="2100-01-01T00:00:00Z")[
            "total"
        ]
        == 1
    )
    with seeded.begin() as conn:
        result = undo.undo_imports(conn, md5s=[imported.md5])
    detail = get(client, f"/imports/{imported.operation_id}")
    assert detail["status"] == "undone"
    assert detail["undos"] == [result.operation_id]
    assert set(detail["per_seller"]) == {"MegaStore", "TechWorld", "ToolShop"}
    get(client, "/imports/99999", expect=404)
    undos = get(client, "/undos")["items"]
    assert undos[0]["undo_kind"] == "import_undo"
    assert undos[0]["targets"] == [imported.operation_id]
    merges = get(client, "/merges")["items"]
    # the seed has "Apple" and "apple": the legacy load recorded one spelling merge
    assert [(m["origin"], m["merged_name"]) for m in merges] == [("legacy_load", "apple")]
    stats = get(client, "/stats")
    assert stats["products"] == 6 and stats["sellers"] == 1
    assert stats["last_import"]["id"] == imported.operation_id


def test_health(client, seeded):
    body = get(client, "/health")
    assert body["status"] == "ok" and body["legacy_loaded"] is True


def test_health_degraded_without_legacy_load(client, seeded):
    with seeded.begin() as conn:
        from sqlalchemy import text

        conn.execute(
            text("UPDATE catalog.operation SET status = 'failed' WHERE kind = 'legacy_load'")
        )
    response = client.get(API + "/health")
    assert response.status_code == 503
    assert scalar(seeded, "SELECT count(*) FROM catalog.operation") == 1


def test_only_get_is_allowed(client):
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)(API + "/products").status_code == 405


def test_security_and_cors_headers(client):
    response = client.get(API + "/brands", headers={"Origin": "http://ui.example"})
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["access-control-allow-origin"] == "http://ui.example"
    preflight = client.options(
        API + "/brands",
        headers={"Origin": "http://ui.example", "Access-Control-Request-Method": "POST"},
    )
    assert preflight.status_code == 400


def test_api_role_cannot_write(reader_engine, seeded):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    with reader_engine.connect() as conn, pytest.raises(DBAPIError):
        conn.execute(text("UPDATE catalog.product SET name = 'hacked'"))
