"""Migrations and the one-time legacy SQLite load (make psqlmigrate)."""

import shutil

import pytest
from sqlalchemy import create_engine, text

from catalog import migrate
from catalog.legacy.sqlite_loader import LegacyLoadError, read_legacy
from helpers import DATA, make_legacy_sqlite


@pytest.fixture
def fresh_db(make_database):
    return make_database("catalog_migration_test")


def query(url, sql):
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql)).all()
    finally:
        engine.dispose()


def test_refuses_without_legacy_file(fresh_db):
    with pytest.raises(Exception, match="make psqlmigrate"):
        migrate.upgrade(fresh_db.owner)
    # the whole upgrade is transactional: nothing was created
    assert migrate.get_state(fresh_db.owner).current is None


def test_legacy_load_of_the_real_catalog(fresh_db, tmp_path):
    legacy = shutil.copy(DATA / "catalog.db", tmp_path / "catalog.db")
    message = migrate.upgrade(fresh_db.owner, legacy_sqlite=str(legacy))
    assert "0002_initial_data" in message
    state = migrate.get_state(fresh_db.owner)
    assert state.at_head and state.legacy_loaded and state.initialized

    (summary,) = query(fresh_db.owner, "SELECT summary FROM catalog.operation")[0]
    assert summary["verified"] is True
    assert summary["products"] == 975
    assert summary["house_offers"] == 975
    assert summary["brand_spellings"] - summary["brands"] == 4
    assert summary["merges"] == 4

    merged = {r[0] for r in query(fresh_db.owner, "SELECT merged_name FROM catalog.merge_detail")}
    assert merged == {"Amazon Basics", "Black+Decker", "Simple Houseware", "Simplehuman"}
    # ids preserved, uuids are v7 and follow id order, VTEX is seller 1
    assert query(fresh_db.owner, "SELECT name FROM catalog.product WHERE id = 1")[0][0] == (
        "Knife Set Professional"
    )
    (ordered,) = query(
        fresh_db.owner,
        "SELECT bool_and(a.uuid < b.uuid AND uuid_extract_version(a.uuid) = 7) "
        "FROM catalog.product a JOIN catalog.product b ON b.id = a.id + 1",
    )[0]
    assert ordered is True
    assert query(fresh_db.owner, "SELECT name FROM catalog.seller WHERE id = 1")[0][0] == "VTEX"
    # sequences continue after the legacy ids
    (next_id,) = query(
        fresh_db.owner, "INSERT INTO catalog.product (name) VALUES ('new one') RETURNING id"
    )[0]
    assert next_id == 976

    # running psqlmigrate again is a no-op
    assert "already migrated" in migrate.upgrade(fresh_db.owner, legacy_sqlite=str(legacy))
    # the source file is never modified
    assert (DATA / "catalog.db").read_bytes() == legacy.read_bytes()


def test_legacy_load_refused_on_database_initialized_empty(fresh_db, tmp_path):
    migrate.upgrade(fresh_db.owner, allow_empty=True)
    legacy = make_legacy_sqlite(tmp_path / "l.db", [(1, "Thing", None, None)])
    with pytest.raises(migrate.MigrationError, match="initialized without the legacy catalog"):
        migrate.upgrade(fresh_db.owner, legacy_sqlite=str(legacy))


def test_missing_legacy_file(fresh_db, tmp_path):
    with pytest.raises(migrate.MigrationError, match="not found"):
        migrate.upgrade(fresh_db.owner, legacy_sqlite=str(tmp_path / "missing.db"))


def test_invalid_legacy_rows_abort_the_load(fresh_db, tmp_path):
    legacy = make_legacy_sqlite(
        tmp_path / "bad.db",
        [(1, "Good product", "Acme", "Tools"), (2, "<script>x</script>", None, None)],
    )
    with pytest.raises(Exception, match="failed validation"):
        migrate.upgrade(fresh_db.owner, legacy_sqlite=str(legacy))
    assert migrate.get_state(fresh_db.owner).current is None


def test_legacy_rows_without_key_abort_the_load(fresh_db, tmp_path):
    legacy = make_legacy_sqlite(tmp_path / "cjk.db", [(1, "東京タワー", None, None)])
    with pytest.raises(Exception, match="EMPTY_KEY"):
        migrate.upgrade(fresh_db.owner, legacy_sqlite=str(legacy))


def test_legacy_seller_links_are_loaded(fresh_db, tmp_path):
    legacy = make_legacy_sqlite(
        tmp_path / "links.db",
        [(1, "Thing One", "Acme", "Tools"), (2, "Thing Two", "ACME", "tools")],
        offers=[("Shop A", 1, 10), ("Shop A", 2, 11), ("shop-a", 2, 12), ("VTEX", 1, 13)],
    )
    migrate.upgrade(fresh_db.owner, legacy_sqlite=str(legacy))
    sellers = query(fresh_db.owner, "SELECT id, name FROM catalog.seller ORDER BY id")
    assert sellers == [(1, "VTEX"), (2, "Shop A")]
    offers = query(
        fresh_db.owner,
        "SELECT seller_id, product_id FROM catalog.seller_product ORDER BY seller_id, product_id",
    )
    assert offers == [(1, 1), (1, 2), (2, 1), (2, 2)]
    assert query(fresh_db.owner, "SELECT count(*) FROM catalog.brand")[0][0] == 1


def test_downgrade_of_initial_data_is_refused(fresh_db):
    migrate.upgrade(fresh_db.owner, allow_empty=True)
    with pytest.raises(Exception, match="irreversible"):
        migrate.downgrade(fresh_db.owner, "0001_schema")


def test_schema_downgrade_and_history(fresh_db):
    migrate.upgrade(fresh_db.owner, "0001_schema")
    assert migrate.get_state(fresh_db.owner).current == "0001_schema"
    assert not migrate.get_state(fresh_db.owner).initialized
    migrate.downgrade(fresh_db.owner, "base")
    assert migrate.get_state(fresh_db.owner).current is None
    assert [line.split(":")[0] for line in migrate.history(fresh_db.owner)] == [
        "0001_schema",
        "0002_initial_data",
    ]


def test_read_legacy_rejects_wrong_schema(tmp_path):
    import sqlite3

    path = tmp_path / "wrong.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE Product (Id INTEGER, Title TEXT)")
    conn.execute("CREATE TABLE SellerProduct (Id INTEGER)")
    conn.close()
    with pytest.raises(LegacyLoadError, match="missing columns"):
        read_legacy(path)


def test_read_legacy_reports_empty_names(tmp_path):
    legacy = make_legacy_sqlite(tmp_path / "e.db", [(1, "   ", None, None)])
    with pytest.raises(LegacyLoadError, match="empty name"):
        read_legacy(legacy)
