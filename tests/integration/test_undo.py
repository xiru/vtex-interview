"""Import undo, undo of an undo (revert) and value-based conflict detection."""

import shutil
from datetime import UTC, datetime, timedelta

import pytest

from catalog.services import undo
from catalog.services.importer import DuplicateFileError, ImportOptions, run_import
from catalog.services.operations import acquire_write_lock
from helpers import fetch_all, make_record, scalar, snapshot, snapshot_active


def run_undo(engine, **kwargs):
    with engine.begin() as conn:
        acquire_write_lock(conn)
        return undo.undo_imports(conn, **kwargs)


def run_revert(engine, op_id):
    with engine.begin() as conn:
        return undo.revert_undo(conn, op_id)


def status(engine, op_id):
    return scalar(engine, "SELECT status FROM catalog.operation WHERE id = :i", i=op_id)


def seller_id(engine, name):
    return scalar(engine, "SELECT id FROM catalog.seller WHERE name = :n", n=name)


@pytest.fixture
def two_seller_file(write_json):
    return write_json(
        [
            make_record("iPhone 15 Pro", "ShopA", "Apple", "Mobile Phones"),  # enrich + offer
            make_record("Brand New Gadget", "ShopA", "Acme", "Gadgets"),  # new product
            make_record("Brand New Gadget", "ShopB", "Acme", "Gadgets"),  # second seller
            make_record("Cable Organizer Kit", "ShopB", "OrgaCo", "Accessories"),  # brand fill
        ],
        name="wrong-file.json",
    )


def test_undo_whole_file_restores_the_catalog(seeded, two_seller_file):
    before = snapshot_active(seeded)
    imported = run_import(seeded, two_seller_file)
    assert snapshot_active(seeded) != before
    result = run_undo(seeded, md5s=[imported.md5])
    assert result.reverted > 0 and not result.conflicts
    assert snapshot_active(seeded) == before
    assert status(seeded, imported.operation_id) == "undone"
    # sellers created by the file are deactivated, the house seller never is
    assert scalar(seeded, "SELECT count(*) FROM catalog.seller WHERE active") == 1


def test_undo_then_revert_then_revert_again(seeded, two_seller_file):
    imported = run_import(seeded, two_seller_file)
    after_import = snapshot(seeded)
    first = run_undo(seeded, md5s=[imported.md5])
    after_undo = snapshot(seeded)

    revert = run_revert(seeded, first.operation_id)
    assert snapshot(seeded) == after_import
    assert status(seeded, imported.operation_id) == "completed"
    assert status(seeded, first.operation_id) == "reverted"

    revert_of_revert = run_revert(seeded, revert.operation_id)
    assert snapshot(seeded) == after_undo
    assert status(seeded, imported.operation_id) == "undone"
    assert status(seeded, first.operation_id) == "completed"
    assert status(seeded, revert.operation_id) == "reverted"

    run_revert(seeded, revert_of_revert.operation_id)
    assert snapshot(seeded) == after_import
    assert status(seeded, imported.operation_id) == "completed"


def test_import_can_be_undone_again_after_a_revert(seeded, two_seller_file):
    before = snapshot_active(seeded)
    imported = run_import(seeded, two_seller_file)
    first = run_undo(seeded, md5s=[imported.md5])
    run_revert(seeded, first.operation_id)
    # effective again: the MD5 guard blocks a duplicate import ...
    with pytest.raises(DuplicateFileError):
        run_import(seeded, two_seller_file)
    # ... and the import can be undone a second time
    run_undo(seeded, md5s=[imported.md5])
    assert snapshot_active(seeded) == before


def test_undo_one_seller_of_a_multi_seller_file(seeded, two_seller_file):
    imported = run_import(seeded, two_seller_file)
    result = run_undo(seeded, md5s=[imported.md5], seller_id=seller_id(seeded, "ShopB"))
    assert result.reverted > 0
    assert status(seeded, imported.operation_id) == "partially_undone"
    active_offers = {
        r[0]
        for r in fetch_all(
            seeded,
            "SELECT s.name FROM catalog.seller_product o JOIN catalog.seller s "
            "ON s.id = o.seller_id WHERE o.active",
        )
    }
    assert active_offers == {"VTEX", "ShopA"}
    # the gadget created by ShopA's record stays (ShopA still offers it)
    assert scalar(seeded, "SELECT active FROM catalog.product WHERE name = 'Brand New Gadget'")


def test_undo_by_seller_keeps_product_still_offered_elsewhere(seeded, write_json):
    path = write_json(
        [
            make_record("Shared Gadget", "ShopA", "Acme"),
            make_record("Shared Gadget", "ShopB", "Acme"),
        ]
    )
    imported = run_import(seeded, path)
    result = run_undo(seeded, md5s=[imported.md5], seller_id=seller_id(seeded, "ShopA"))
    # product created by ShopA's record, but ShopB still offers it
    assert any("still offered by seller" in line for line in result.kept)
    assert scalar(seeded, "SELECT active FROM catalog.product WHERE name = 'Shared Gadget'")


def test_all_files_window_with_seller(seeded, write_json):
    a = run_import(seeded, write_json([make_record("Gadget A", "ShopA")]))
    b = run_import(
        seeded, write_json([make_record("Gadget B", "ShopA"), make_record("Gadget C", "ShopB")])
    )
    since = datetime.now(UTC) - timedelta(hours=1)
    result = run_undo(seeded, all_files=True, since=since, seller_id=seller_id(seeded, "ShopA"))
    assert sorted(result.target_operations) == sorted([a.operation_id, b.operation_id])
    names = {
        r[0]
        for r in fetch_all(
            seeded, "SELECT name FROM catalog.product WHERE active AND name LIKE 'Gadget%'"
        )
    }
    assert names == {"Gadget C"}


def test_time_window_excludes_older_changes(seeded, write_json):
    run_import(seeded, write_json([make_record("Gadget A", "ShopA")]))
    future = datetime.now(UTC) + timedelta(hours=1)
    with pytest.raises(undo.UndoError, match="nothing to undo"):
        run_undo(seeded, all_files=True, since=future)
    with pytest.raises(undo.UndoError, match="nothing to undo"):
        run_undo(
            seeded,
            all_files=True,
            since=datetime(2000, 1, 1, tzinfo=UTC),
            until=datetime(2001, 1, 1, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({}, "exactly one"),
        ({"md5s": ["0" * 32], "all_files": True}, "exactly one"),
        ({"all_files": True}, "requires --since"),
        ({"md5s": ["abc"]}, "full 32-character"),
        ({"md5s": ["0123456789abcdef0123456789abcde"]}, "full 32-character"),
        ({"md5s": ["0" * 32]}, "no import found"),
    ],
)
def test_invalid_requests(seeded, kwargs, message):
    with pytest.raises(undo.UndoError, match=message):
        run_undo(seeded, **kwargs)


def test_renamed_file_matches_by_hash_and_same_name_files_stay_separate(
    seeded, tmp_path, write_json
):
    first = write_json([make_record("Gadget A", "ShopA")], name="catalog.json")
    imported = run_import(seeded, first)
    renamed = tmp_path / "renamed.json"
    shutil.move(first, renamed)
    other = write_json([make_record("Gadget B", "ShopA")], name="catalog.json")  # same name
    second = run_import(seeded, other)
    assert second.md5 != imported.md5
    run_undo(seeded, md5s=[imported.md5])
    assert status(seeded, imported.operation_id) == "undone"
    assert status(seeded, second.operation_id) == "completed"


def test_several_hashes(seeded, write_json):
    a = run_import(seeded, write_json([make_record("Gadget A", "ShopA")]))
    b = run_import(seeded, write_json([make_record("Gadget B", "ShopA")]))
    result = run_undo(seeded, md5s=[a.md5, b.md5.upper()])
    assert sorted(result.target_operations) == sorted([a.operation_id, b.operation_id])


def test_conflict_aborts_and_skip_conflicts_proceeds(seeded, write_json):
    rid = make_record("x")["Id"]
    first = run_import(
        seeded,
        write_json(
            [make_record("Gadget A", "ShopA", record_id=rid), make_record("Gadget B", "ShopA")]
        ),
    )
    # a later import changes the same offer (raw name), then the first import is undone
    run_import(
        seeded, write_json([make_record("Gadget A (2026 edition)", "ShopA", record_id=rid)])
    )
    before = snapshot(seeded)
    with pytest.raises(undo.UndoConflictError) as info:
        run_undo(seeded, md5s=[first.md5])
    assert "seller_product" in str(info.value)
    assert info.value.conflicts[0].last_operation_id is not None
    assert snapshot(seeded) == before  # all-or-nothing

    result = run_undo(seeded, md5s=[first.md5], skip_conflicts=True)
    assert result.conflicts
    assert status(seeded, first.operation_id) == "partially_undone"
    assert not scalar(seeded, "SELECT active FROM catalog.product WHERE name = 'Gadget B'")
    assert scalar(
        seeded, "SELECT active FROM catalog.seller_product WHERE seller_product_id = :r", r=rid
    )


def test_revert_is_all_or_nothing(seeded, write_json):
    rid = make_record("x")["Id"]
    imported = run_import(seeded, write_json([make_record("Gadget A", "ShopA", record_id=rid)]))
    first = run_undo(seeded, md5s=[imported.md5])
    # a new import reactivates the same offer: reverting the undo now conflicts
    run_import(seeded, write_json([make_record("Gadget A2", "ShopA", record_id=rid)]))
    before = snapshot(seeded)
    with pytest.raises(undo.UndoConflictError):
        run_revert(seeded, first.operation_id)
    assert snapshot(seeded) == before


def test_reimport_after_undo_reactivates_rows(seeded, write_json):
    path = write_json([make_record("Gadget A", "ShopA", "Acme", "Toys")])
    imported = run_import(seeded, path)
    product_id = scalar(seeded, "SELECT id FROM catalog.product WHERE name = 'Gadget A'")
    run_undo(seeded, md5s=[imported.md5])
    again = run_import(seeded, path)  # allowed: the previous import is undone
    assert again.counters["products_created"] == 0
    assert again.counters["duplicate_products"] == 1
    assert scalar(seeded, "SELECT id FROM catalog.product WHERE name = 'Gadget A' AND active") == (
        product_id
    )
    assert scalar(seeded, "SELECT count(*) FROM catalog.product WHERE name = 'Gadget A'") == 1


def test_revert_requires_an_undo_operation(seeded, write_json):
    imported = run_import(seeded, write_json([make_record("Gadget A", "ShopA")]))
    with pytest.raises(undo.UndoError, match="not an undo"):
        run_revert(seeded, imported.operation_id)
    first = run_undo(seeded, md5s=[imported.md5])
    run_revert(seeded, first.operation_id)
    with pytest.raises(undo.UndoError, match="nothing left"):
        run_revert(seeded, first.operation_id)


def test_legacy_load_cannot_be_undone(seeded):
    with pytest.raises(undo.UndoError):
        run_revert(seeded, 1)
    assert scalar(seeded, "SELECT kind FROM catalog.operation WHERE id = 1") == "legacy_load"
    with seeded.begin() as conn, pytest.raises(undo.UndoError):
        undo.undo_merge(conn, 1)


def test_dry_run_style_rollback_leaves_state(seeded, write_json):
    imported = run_import(seeded, write_json([make_record("Gadget A", "ShopA")]))
    before = snapshot(seeded)
    with seeded.connect() as conn:
        trans = conn.begin()
        undo.undo_imports(conn, md5s=[imported.md5])
        trans.rollback()
    assert snapshot(seeded) == before


def test_force_reimport_twice_then_undo_window(seeded, write_json):
    rid = make_record("x")["Id"]
    first_file = write_json([make_record("Gadget A", "ShopA", record_id=rid)])
    first = run_import(seeded, first_file)
    # force re-import of the same file after the catalog moved on
    run_import(seeded, write_json([make_record("Gadget A v2", "ShopA", record_id=rid)]))
    second = run_import(seeded, first_file, ImportOptions(force=True))
    assert second.counters["offers_updated"] == 1
    # undo only the latest run of that file: a window starting after the first run
    started = scalar(
        seeded, "SELECT started_at FROM catalog.operation WHERE id = :i", i=second.operation_id
    )
    result = run_undo(seeded, md5s=[first.md5], since=started)
    assert result.target_operations == [second.operation_id]
    assert (
        scalar(
            seeded, "SELECT name FROM catalog.seller_product WHERE seller_product_id = :r", r=rid
        )
        == "Gadget A v2"
    )
