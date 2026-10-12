"""Import of seller files: matching, counters, conflicts, failures and the operation record."""

import json
import logging
import threading
import uuid

import pytest
from sqlalchemy import text

from catalog.config import IdPolicy
from catalog.services.importer import (
    DuplicateFileError,
    ImportFileError,
    ImportOptions,
    file_digest,
    run_import,
)
from catalog.services.operations import LockBusyError, acquire_write_lock
from helpers import DATA, make_record, scalar


def rows(engine, sql, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql), params).all()


def test_sample_file(seeded, tmp_path):
    result = run_import(seeded, DATA / "ProductEntry.json")
    c = result.counters
    assert c["total_records"] == 269
    assert c["invalid_records"] == 3
    assert c["sellers_created"] == 20
    assert c["offers_created"] + c["offers_updated"] + c["unchanged"] + c["skipped_records"] == 266
    assert result.md5 == file_digest(DATA / "ProductEntry.json")[0]
    assert {code for _, r in result.rejections for code in [r.code]} >= {
        "INVALID_ID_FORMAT",
        "SQL_INJECTION",
    }
    batch = rows(seeded, "SELECT * FROM catalog.import_batch")[0]
    assert batch.md5 == result.md5
    assert batch.file_size == (DATA / "ProductEntry.json").stat().st_size
    assert batch.file_name == "ProductEntry.json"
    assert batch.id_validation == "lenient;versions=4,7"
    assert batch.total_records == 269
    assert (
        scalar(seeded, "SELECT status FROM catalog.operation WHERE id = :i", i=result.operation_id)
        == "completed"
    )
    # the injection probe never reached the database
    assert scalar(seeded, "SELECT count(*) FROM catalog.brand WHERE name LIKE '%SELECT%'") == 0


def test_new_and_duplicate_products(seeded, write_json):
    path = write_json(
        [
            make_record("iPhone 15 Pro", "ShopA", "APPLE", "Electronics"),  # duplicate of 1
            make_record("Brand New Gadget", "ShopA", "Acme", "Gadgets"),  # new
            make_record("Brand  New  Gadget", "ShopB", "acme", "gadgets"),  # dup of the new one
        ]
    )
    result = run_import(seeded, path)
    c = result.counters
    assert (c["products_created"], c["duplicate_products"], c["offers_created"]) == (1, 2, 3)
    assert c["sellers_created"] == 2
    assert scalar(seeded, "SELECT count(*) FROM catalog.product") == 7
    sellers_of_iphone = rows(
        seeded,
        "SELECT s.name FROM catalog.seller_product o JOIN catalog.seller s ON s.id = o.seller_id "
        "WHERE o.product_id = 1 ORDER BY s.name",
    )
    assert [r.name for r in sellers_of_iphone] == ["ShopA", "VTEX"]
    # brand and category spellings resolve to one row each
    assert (
        scalar(seeded, "SELECT count(*) FROM catalog.brand WHERE catalog.name_key(name) = 'acme'")
        == 1
    )
    assert (
        scalar(
            seeded, "SELECT name FROM catalog.category WHERE catalog.name_key(name) = 'gadgets'"
        )
        == "gadgets"
    )
    # the offer keeps the seller's raw spelling
    raw = rows(
        seeded,
        "SELECT name, brand_name FROM catalog.seller_product WHERE product_id = 1 "
        "AND seller_id <> 1",
    )[0]
    assert (raw.name, raw.brand_name) == ("iPhone 15 Pro", "APPLE")


def test_enrichment_adds_category_and_brand(seeded, write_json):
    path = write_json(
        [
            # iPhone gains a second category
            make_record("iPhone 15 Pro", "ShopA", "Apple", "Mobile Phones"),
            # unbranded legacy product gets a brand
            make_record("Cable Organizer Kit", "ShopA", "OrgaCo", "Accessories"),
        ]
    )
    result = run_import(seeded, path)
    assert result.counters["products_updated"] == 2
    cats = rows(
        seeded,
        "SELECT c.name FROM catalog.product_category pc JOIN catalog.category c "
        "ON c.id = pc.category_id WHERE pc.product_id = 1 ORDER BY 1",
    )
    assert [r.name for r in cats] == ["electronics", "mobile phones"]
    assert (
        scalar(
            seeded,
            "SELECT b.name FROM catalog.product p JOIN catalog.brand b "
            "ON b.id = p.brand_id WHERE p.id = 4",
        )
        == "OrgaCo"
    )


def test_record_without_brand_matches_single_candidate(seeded, write_json):
    result = run_import(seeded, write_json([make_record("iphone 15 pro", "ShopA")]))
    assert result.counters["duplicate_products"] == 1
    assert result.counters["products_created"] == 0


def test_ambiguous_match_is_skipped(seeded, write_json, caplog):
    run_import(seeded, write_json([make_record("Universal Remote", "ShopA", "Sony")]))
    run_import(seeded, write_json([make_record("Universal Remote", "ShopA", "LG")]))
    caplog.set_level(logging.WARNING, logger="catalog.import")
    result = run_import(seeded, write_json([make_record("Universal Remote", "ShopB")]))
    assert result.counters["skipped_records"] == 1
    assert result.rejections[0][1].code == "AMBIGUOUS_MATCH"
    assert "AMBIGUOUS_MATCH" in caplog.text
    # nothing was created for the skipped record (seller ShopB does not exist)
    assert scalar(seeded, "SELECT count(*) FROM catalog.seller WHERE name = 'ShopB'") == 0


def test_duplicate_offer_is_skipped(seeded, write_json):
    path = write_json(
        [
            make_record("iPhone 15 Pro", "ShopA", "Apple"),
            make_record("iPhone 15  Pro", "ShopA", "Apple"),  # same product, other seller id
        ]
    )
    result = run_import(seeded, path)
    assert result.counters["offers_created"] == 1
    assert result.counters["skipped_records"] == 1
    assert result.rejections[0][1].code == "DUPLICATE_OFFER"


def test_same_seller_id_twice_updates_the_offer(seeded, write_json):
    rid = str(uuid.uuid4())
    path = write_json(
        [
            make_record("Câmera Canon EOS R6", "ShopA", "Canon", "Photo", rid),
            make_record("Camera Canon EOS R6", "ShopA", "Canon", "Photography", rid),
        ]
    )
    result = run_import(seeded, path)
    c = result.counters
    assert (c["products_created"], c["offers_created"], c["offers_updated"]) == (1, 1, 1)
    offer = rows(
        seeded,
        "SELECT name, category_name FROM catalog.seller_product WHERE seller_product_id = :r",
        r=rid,
    )[0]
    assert (offer.name, offer.category_name) == ("Camera Canon EOS R6", "Photography")


def test_seller_id_reused_for_a_different_product_repoints(seeded, write_json):
    rid = str(uuid.uuid4())
    run_import(seeded, write_json([make_record("iPhone 15 Pro", "ShopA", "Apple", None, rid)]))
    result = run_import(
        seeded, write_json([make_record("MacBook Air M2", "ShopA", "Apple", None, rid)])
    )
    assert result.counters["offers_updated"] == 1
    assert (
        scalar(
            seeded,
            "SELECT product_id FROM catalog.seller_product WHERE seller_product_id = :r",
            r=rid,
        )
        == 2
    )


def test_different_sellers_may_share_ids(seeded, write_json):
    rid = str(uuid.uuid4())
    result = run_import(
        seeded,
        write_json(
            [
                make_record("iPhone 15 Pro", "ShopA", "Apple", None, rid),
                make_record("Knife Set Professional", "ShopB", "Wusthof", None, rid),
            ]
        ),
    )
    assert result.counters["offers_created"] == 2


def test_reimport_is_unchanged_and_md5_guard(seeded, write_json):
    path = write_json([make_record("iPhone 15 Pro", "ShopA", "Apple", "Electronics")])
    run_import(seeded, path)
    with pytest.raises(DuplicateFileError, match="already imported"):
        run_import(seeded, path)
    failed = rows(
        seeded,
        "SELECT status, error_message FROM catalog.operation WHERE kind = 'import' ORDER BY id",
    )[-1]
    assert failed.status == "failed" and "DuplicateFileError" in failed.error_message
    forced = run_import(seeded, path, ImportOptions(force=True))
    assert forced.counters["unchanged"] == 1
    assert forced.counters["offers_created"] == 0


def test_unknown_seller_rejected_with_no_create(seeded, write_json):
    path = write_json(
        [
            make_record("iPhone 15 Pro", "Nobody", "Apple"),
            make_record("House Exclusive Item", "vtex", "Acme"),
        ]
    )
    result = run_import(seeded, path, ImportOptions(create_sellers=False))
    assert result.counters["skipped_records"] == 1
    assert result.rejections[0][1].code == "UNKNOWN_SELLER"
    # "vtex" resolves to the house seller (any spelling), so no seller is created
    assert result.counters["sellers_created"] == 0
    assert result.counters["offers_created"] == 1
    assert (
        scalar(
            seeded,
            "SELECT seller_id FROM catalog.seller_product o JOIN catalog.product p "
            "ON p.id = o.product_id WHERE p.name = 'House Exclusive Item'",
        )
        == 1
    )


def test_dry_run_changes_nothing(seeded, write_json):
    before = scalar(seeded, "SELECT count(*) FROM catalog.operation")
    result = run_import(
        seeded, write_json([make_record("New Thing", "ShopA")]), ImportOptions(dry_run=True)
    )
    assert result.dry_run and result.operation_id is None
    assert result.counters["products_created"] == 1
    assert scalar(seeded, "SELECT count(*) FROM catalog.operation") == before
    assert scalar(seeded, "SELECT count(*) FROM catalog.seller") == 1


def test_strict_policy_and_empty_keys(seeded, write_json):
    bad_variant = "66778899-0011-4223-3445-567788990234"
    path = write_json(
        [
            make_record("Thing", "ShopA", record_id=bad_variant),
            make_record("東京タワー", "ShopA"),
        ]
    )
    result = run_import(seeded, path, ImportOptions(policy=IdPolicy(mode="strict")))
    codes = sorted(r.code for _, r in result.rejections)
    assert codes == ["EMPTY_KEY", "INVALID_ID_VARIANT"]
    assert result.counters["invalid_records"] == 2
    assert (
        scalar(seeded, "SELECT id_validation FROM catalog.import_batch") == "strict;versions=4,7"
    )


def test_unreadable_files_are_recorded_as_failed(seeded, tmp_path):
    not_array = tmp_path / "object.json"
    not_array.write_text(json.dumps({"Id": 1}))
    with pytest.raises(ImportFileError, match="JSON array"):
        run_import(seeded, not_array)
    broken = tmp_path / "broken.json"
    broken.write_text("[{")
    with pytest.raises(ImportFileError):
        run_import(seeded, broken)
    statuses = rows(seeded, "SELECT status FROM catalog.operation WHERE kind = 'import'")
    assert [s.status for s in statuses] == ["failed", "failed"]
    with pytest.raises(ImportFileError, match="not found"):
        run_import(seeded, tmp_path / "missing.json")


def test_failure_midway_leaves_no_partial_data(seeded, write_json, monkeypatch):
    from catalog.services import importer

    calls = {"n": 0}
    original = importer._Importer.process

    def explode(self, index, rec):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom")
        return original(self, index, rec)

    monkeypatch.setattr(importer._Importer, "process", explode)
    path = write_json([make_record("Thing One", "ShopA"), make_record("Thing Two", "ShopA")])
    with pytest.raises(RuntimeError, match="boom"):
        run_import(seeded, path)
    assert scalar(seeded, "SELECT count(*) FROM catalog.product WHERE name LIKE 'Thing%'") == 0
    status, error = rows(
        seeded, "SELECT status, error_message FROM catalog.operation WHERE kind = 'import'"
    )[0]
    assert status == "failed" and "boom" in error


def test_no_wait_fails_fast_when_locked(seeded, write_json):
    path = write_json([make_record("Thing", "ShopA")])
    holding = threading.Event()
    release = threading.Event()

    def hold_lock():
        with seeded.begin() as conn:
            acquire_write_lock(conn)
            holding.set()
            release.wait(10)

    worker = threading.Thread(target=hold_lock)
    worker.start()
    holding.wait(10)
    try:
        with pytest.raises(LockBusyError):
            run_import(seeded, path, ImportOptions(wait=False))
    finally:
        release.set()
        worker.join()


def test_change_log_records_every_write(seeded, write_json):
    result = run_import(
        seeded, write_json([make_record("Fresh Product", "ShopA", "Acme", "Toys")])
    )
    logged = rows(
        seeded,
        "SELECT table_name, action, seller_id FROM catalog.change_log "
        "WHERE operation_id = :op ORDER BY id",
        op=result.operation_id,
    )
    assert [(r.table_name, r.action) for r in logged] == [
        ("seller", "insert"),
        ("brand", "insert"),
        ("category", "insert"),
        ("product", "insert"),
        ("product_category", "insert"),
        ("seller_product", "insert"),
    ]
    assert len({r.seller_id for r in logged}) == 1
