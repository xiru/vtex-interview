"""Database-level rules: key function, uniqueness, CHECKs, UUIDv7, triggers, roles."""

import time

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from helpers import scalar


@pytest.mark.parametrize(
    ("value", "key"),
    [
        ("Câmera Canon EOS-R6", "cameracanoneosr6"),
        ("Amazon Basics", "amazonbasics"),
        ("AmazonBasics", "amazonbasics"),
        ("BLACK+DECKER", "blackdecker"),
        ("Black & Decker", "blackdecker"),
        ("Levi's", "levis"),
        ("Ørsted", "orsted"),
        ("Straße", "strasse"),
        ("ＡＢＣ１２３", "abc123"),
        ("Œuvre Łódź", "oeuvrelodz"),
        ("東京", ""),
        ("!!!", ""),
    ],
)
def test_name_key(clean_db, value, key):
    assert scalar(clean_db, "SELECT catalog.name_key(:v)", v=value) == key


def test_name_fold_keeps_words(clean_db):
    assert scalar(clean_db, "SELECT catalog.name_fold(:v)", v="  Hugo-BOSS  Ørsted ") == (
        "hugo boss orsted"
    )


def _insert(engine, sql, **params):
    with engine.begin() as conn:
        result = conn.execute(text(sql), params)
        return result.scalar() if result.returns_rows else None


def test_brand_uniqueness_ignores_case_accents_and_punctuation(clean_db):
    _insert(clean_db, "INSERT INTO catalog.brand (name) VALUES ('Black+Decker')")
    for spelling in ("BLACK+DECKER", "black decker", "Black-Decker"):
        with pytest.raises(IntegrityError):
            _insert(clean_db, "INSERT INTO catalog.brand (name) VALUES (:n)", n=spelling)


@pytest.mark.parametrize("table", ["seller", "brand", "category", "product"])
def test_names_need_latin_letters_or_digits(clean_db, table):
    with pytest.raises(IntegrityError, match="name_key_not_empty"):
        _insert(clean_db, f"INSERT INTO catalog.{table} (name) VALUES ('!!!')")


def test_category_must_be_lowercase(clean_db):
    with pytest.raises(IntegrityError, match="category_name_lowercase"):
        _insert(clean_db, "INSERT INTO catalog.category (name) VALUES ('Electronics')")
    _insert(clean_db, "INSERT INTO catalog.category (name) VALUES ('electronics')")
    with pytest.raises(IntegrityError):
        _insert(clean_db, "INSERT INTO catalog.category (name) VALUES ('electr-onics')")


def test_product_unique_name_and_brand_with_nulls_not_distinct(clean_db):
    brand = _insert(clean_db, "INSERT INTO catalog.brand (name) VALUES ('Apple') RETURNING id")
    _insert(clean_db, "INSERT INTO catalog.product (name) VALUES ('Cable Kit')")
    with pytest.raises(IntegrityError, match="product_name_key_brand_uq"):
        _insert(clean_db, "INSERT INTO catalog.product (name) VALUES ('cable-kit')")
    _insert(
        clean_db, "INSERT INTO catalog.product (name, brand_id) VALUES ('Cable Kit', :b)", b=brand
    )
    with pytest.raises(IntegrityError):
        _insert(
            clean_db,
            "INSERT INTO catalog.product (name, brand_id) VALUES ('CABLE KIT', :b)",
            b=brand,
        )


def test_product_uuid_defaults_to_v7(clean_db):
    value = _insert(clean_db, "INSERT INTO catalog.product (name) VALUES ('X') RETURNING uuid")
    assert value.version == 7


@pytest.mark.parametrize(
    "value",
    [
        "66778899-0011-4223-3445-567788990234",  # non-RFC variant: version() is NULL
        "a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d",  # a valid v4
    ],
)
def test_product_uuid_must_be_v7_null_safe(clean_db, value):
    with pytest.raises(IntegrityError, match="product_uuid_is_v7"):
        _insert(clean_db, "INSERT INTO catalog.product (name, uuid) VALUES ('X', :u)", u=value)


def test_md5_shape_is_checked(clean_db):
    op = _insert(
        clean_db,
        "INSERT INTO catalog.operation (kind, status) VALUES ('import','running') RETURNING id",
    )
    with pytest.raises(IntegrityError):
        _insert(
            clean_db,
            "INSERT INTO catalog.import_batch (operation_id, file_name, md5, file_size, "
            "id_validation) VALUES (:op, 'f', 'NOT-A-HASH', 1, 'lenient')",
            op=op,
        )


def test_updated_trigger(clean_db):
    created = _insert(
        clean_db, "INSERT INTO catalog.brand (name) VALUES ('Acme') RETURNING updated"
    )
    time.sleep(0.01)
    updated = _insert(clean_db, "UPDATE catalog.brand SET name = 'ACME Inc' RETURNING updated")
    assert updated > created


def test_reader_role_is_read_only(clean_db, reader_engine):
    with reader_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM catalog.seller")).scalar() == 1
        with pytest.raises(DBAPIError, match="read-only"):
            conn.execute(text("INSERT INTO catalog.brand (name) VALUES ('nope')"))
