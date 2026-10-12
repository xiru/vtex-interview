"""Merges, unmerges (merge undo) and near-duplicate suggestions."""

import pytest

from catalog.services import duplicates, merge, undo
from catalog.services.importer import run_import
from helpers import fetch_all, make_record, scalar, snapshot, snapshot_active


def run(engine, fn, *args, **kwargs):
    with engine.begin() as conn:
        return fn(conn, *args, **kwargs)


def product_id(engine, name):
    return scalar(engine, "SELECT id FROM catalog.product WHERE name = :n", n=name)


@pytest.fixture
def near_duplicates(seeded, write_json):
    """Two products that are the same item, offered by different sellers."""
    run_import(
        seeded,
        write_json(
            [
                make_record("Processor AMD Ryzen 9 7950X", "ShopA", "AMD", "Components"),
                make_record("Processador AMD Ryzen 9 7950X", "ShopB", "AMD", "CPUs"),
                make_record("Processador AMD Ryzen 9 7950X", "ShopC", "AMD", "CPUs"),
                make_record("Processor AMD Ryzen 9 7950X", "ShopC", "AMD", "Components"),
            ]
        ),
    )
    return seeded


def test_duplicate_candidates(near_duplicates):
    with near_duplicates.connect() as conn:
        found = duplicates.find_candidates(conn, "product", threshold=0.5)
    pair = next(c for c in found if "Ryzen" in c.keep_name)
    assert {pair.keep_name, pair.duplicate_name} == {
        "Processor AMD Ryzen 9 7950X",
        "Processador AMD Ryzen 9 7950X",
    }
    assert pair.keep_brand == "AMD" and pair.keep_offers == 2
    assert duplicates.merge_command("product", pair).startswith("catalog merge product --keep")


def test_duplicate_candidates_for_brands_and_categories(seeded, write_json):
    run_import(
        seeded,
        write_json(
            [
                make_record("X1", "ShopA", "Hugo"),
                make_record("X2", "ShopA", "Hugo Boss", "Home Decor"),
                make_record("X3", "ShopA", None, "Home"),
            ]
        ),
    )
    with seeded.connect() as conn:
        brands = duplicates.find_candidates(conn, "brand", 0.6)
        categories = duplicates.find_candidates(conn, "category", 0.6)
    assert ("Hugo", "Hugo Boss") in {(c.keep_name, c.duplicate_name) for c in brands}
    assert ("home decor", "home") in {(c.keep_name, c.duplicate_name) for c in categories} or (
        "home",
        "home decor",
    ) in {(c.keep_name, c.duplicate_name) for c in categories}


def test_duplicate_candidates_validation(seeded):
    with seeded.connect() as conn:
        with pytest.raises(ValueError):
            duplicates.find_candidates(conn, "seller")
        with pytest.raises(ValueError):
            duplicates.find_candidates(conn, "brand", threshold=0)


def test_product_merge_and_unmerge(near_duplicates):
    engine = near_duplicates
    keep = product_id(engine, "Processor AMD Ryzen 9 7950X")
    dup = product_id(engine, "Processador AMD Ryzen 9 7950X")
    before = snapshot_active(engine)
    result = run(engine, merge.merge_products, keep, [dup])
    # ShopC offered both: its duplicate offer is deactivated, not moved
    assert any("offers both products" in note for note in result.notes)
    offers = fetch_all(
        engine,
        "SELECT s.name FROM catalog.seller_product o JOIN catalog.seller s "
        "ON s.id = o.seller_id WHERE o.product_id = :p AND o.active "
        "ORDER BY 1",
        p=keep,
    )
    assert [o.name for o in offers] == ["ShopA", "ShopB", "ShopC"]
    cats = fetch_all(
        engine,
        "SELECT c.name FROM catalog.product_category pc JOIN catalog.category "
        "c ON c.id = pc.category_id WHERE pc.product_id = :p AND pc.active "
        "ORDER BY 1",
        p=keep,
    )
    assert [c.name for c in cats] == ["components", "cpus"]
    merged = fetch_all(
        engine, "SELECT active, merged_into_id FROM catalog.product WHERE id = :p", p=dup
    )[0]
    assert (merged.active, merged.merged_into_id) == (False, keep)

    unmerged = run(engine, undo.undo_merge, result.operation_id)
    assert unmerged.reverted > 0
    # rows are never deleted: links the merge created stay, inactive
    assert snapshot_active(engine) == before
    assert (
        scalar(engine, "SELECT status FROM catalog.operation WHERE id = :i", i=result.operation_id)
        == "undone"
    )

    # undoing the unmerge re-applies the merge
    run(engine, undo.revert_undo, unmerged.operation_id)
    assert not scalar(engine, "SELECT active FROM catalog.product WHERE id = :p", p=dup)


def test_import_after_merge_follows_merged_into(near_duplicates, write_json):
    engine = near_duplicates
    keep = product_id(engine, "Processor AMD Ryzen 9 7950X")
    dup = product_id(engine, "Processador AMD Ryzen 9 7950X")
    result = run(engine, merge.merge_products, keep, [dup])
    run_import(engine, write_json([make_record("Processador AMD Ryzen 9 7950X", "ShopD", "AMD")]))
    assert (
        scalar(
            engine,
            "SELECT o.product_id FROM catalog.seller_product o JOIN catalog.seller s "
            "ON s.id = o.seller_id WHERE s.name = 'ShopD'",
        )
        == keep
    )
    # unmerge leaves the later offer on the kept product and reports it
    unmerged = run(engine, undo.undo_merge, result.operation_id)
    assert any("created after the merge" in note for note in unmerged.notes)


def test_unmerge_conflicts_with_later_changes(near_duplicates, write_json):
    engine = near_duplicates
    keep = product_id(engine, "Processor AMD Ryzen 9 7950X")
    dup = product_id(engine, "Processador AMD Ryzen 9 7950X")
    shop_b_offer = fetch_all(
        engine,
        "SELECT o.seller_product_id FROM catalog.seller_product o "
        "JOIN catalog.seller s ON s.id = o.seller_id "
        "WHERE s.name = 'ShopB'",
    )[0][0]
    result = run(engine, merge.merge_products, keep, [dup])
    # ShopB updates its (moved) offer afterwards
    run_import(
        engine,
        write_json(
            [
                make_record(
                    "Processor AMD Ryzen 9 7950X Box", "ShopB", "AMD", record_id=str(shop_b_offer)
                )
            ]
        ),
    )
    before = snapshot(engine)
    with pytest.raises(undo.UndoConflictError):
        run(engine, undo.undo_merge, result.operation_id)
    assert snapshot(engine) == before


@pytest.mark.parametrize(
    ("keep", "dups", "message"),
    [
        (1, [1], "must be different"),
        (1, [], "at least one"),
        (1, [2, 2], "repeated"),
        (1, [999], "does not exist"),
    ],
)
def test_product_merge_validation(seeded, keep, dups, message):
    with pytest.raises(merge.MergeError, match=message):
        run(seeded, merge.merge_products, keep, dups)


def test_cannot_merge_into_an_already_merged_product(seeded):
    run(seeded, merge.merge_products, 1, [2])
    with pytest.raises(merge.MergeError, match="inactive or already merged"):
        run(seeded, merge.merge_products, 2, [3])


def test_brand_merge_with_collisions(seeded, write_json):
    run_import(
        seeded,
        write_json(
            [
                make_record("Gizmo", "ShopA", "Acme"),
                make_record("Gizmo", "ShopA", "Acme Corp"),
                make_record("Widget", "ShopA", "Acme Corp"),
            ]
        ),
    )
    keep = scalar(seeded, "SELECT id FROM catalog.brand WHERE name = 'Acme'")
    dup = scalar(seeded, "SELECT id FROM catalog.brand WHERE name = 'Acme Corp'")
    before = snapshot(seeded)
    with pytest.raises(merge.MergeConflictError) as info:
        run(seeded, merge.merge_brands, keep, dup)
    assert len(info.value.pairs) == 1
    assert snapshot(seeded) == before

    result = run(seeded, merge.merge_brands, keep, dup, merge_colliding_products=True)
    assert any("merged into" in note for note in result.notes)
    assert (
        scalar(
            seeded, "SELECT count(*) FROM catalog.product WHERE brand_id = :b AND active", b=keep
        )
        == 2
    )
    assert not scalar(seeded, "SELECT active FROM catalog.brand WHERE id = :b", b=dup)

    run(seeded, undo.undo_merge, result.operation_id)
    assert snapshot(seeded) == before


def test_category_merge_and_unmerge(seeded, write_json):
    run_import(
        seeded,
        write_json(
            [
                make_record("iPhone 15 Pro", "ShopA", "Apple", "Smartphones"),
                make_record("Galaxy Tab", "ShopA", "Samsung", "Smartphones"),
            ]
        ),
    )
    keep = scalar(seeded, "SELECT id FROM catalog.category WHERE name = 'electronics'")
    dup = scalar(seeded, "SELECT id FROM catalog.category WHERE name = 'smartphones'")
    before = snapshot_active(seeded)
    result = run(seeded, merge.merge_categories, keep, dup)
    assert any("already in category" in note for note in result.notes)  # the iPhone
    linked = fetch_all(
        seeded,
        "SELECT p.name FROM catalog.product_category pc JOIN catalog.product "
        "p ON p.id = pc.product_id WHERE pc.category_id = :c AND pc.active "
        "ORDER BY 1",
        c=keep,
    )
    assert "Galaxy Tab" in [r.name for r in linked]
    run(seeded, undo.undo_merge, result.operation_id)
    assert snapshot_active(seeded) == before


def test_merge_fills_missing_brand(seeded, write_json):
    run_import(seeded, write_json([make_record("Cable Organiser Kit", "ShopA", "OrgaCo")]))
    keep = product_id(seeded, "Cable Organizer Kit")  # legacy product without brand
    dup = product_id(seeded, "Cable Organiser Kit")
    run(seeded, merge.merge_products, keep, [dup])
    assert (
        scalar(
            seeded,
            "SELECT b.name FROM catalog.product p JOIN catalog.brand b "
            "ON b.id = p.brand_id WHERE p.id = :p",
            p=keep,
        )
        == "OrgaCo"
    )


def test_undo_merge_requires_a_merge(seeded, write_json):
    imported = run_import(seeded, write_json([make_record("Thing", "ShopA")]))
    with pytest.raises(undo.UndoError, match="not a manual merge"):
        run(seeded, undo.undo_merge, imported.operation_id)
    result = run(seeded, merge.merge_products, 1, [2])
    run(seeded, undo.undo_merge, result.operation_id)
    with pytest.raises(undo.UndoError, match="already undone"):
        run(seeded, undo.undo_merge, result.operation_id)
