"""Whole-record validation and display cleaning."""

import json
from collections import Counter
from pathlib import Path

import pytest

from catalog.config import IdPolicy
from catalog.validation import Rejection, ValidRecord, validate_record
from catalog.validation.text import display_clean, optional_clean

GOOD = {
    "Id": "a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d",
    "SellerName": " MegaStore ",
    "Name": "Smartphone   Galaxy\tS23",
    "Brand": "Samsung",
    "Category": "  ",
}


def test_valid_record_is_cleaned():
    result = validate_record(GOOD, IdPolicy())
    assert isinstance(result, ValidRecord)
    assert result.seller_name == "MegaStore"
    assert result.name == "Smartphone Galaxy S23"
    assert result.brand == "Samsung"
    assert result.category is None
    assert str(result.seller_product_id) == GOOD["Id"]


@pytest.mark.parametrize(
    ("change", "code", "field"),
    [
        ({"Name": None}, "BAD_TYPE", "Name"),
        ({"Name": 42}, "BAD_TYPE", "Name"),
        ({"Brand": ["x"]}, "BAD_TYPE", "Brand"),
        ({"Name": "x" * 301}, "TOO_LONG", "Name"),
        ({"Brand": "b" * 121}, "TOO_LONG", "Brand"),
        ({"Name": "   "}, "MISSING_FIELD", "Name"),
        ({"SellerName": " "}, "MISSING_FIELD", "SellerName"),
        ({"Brand": "<b>x</b>"}, "MARKUP", "Brand"),
        ({"SellerName": "x'; drop table seller; --"}, "SQL_INJECTION", "SellerName"),
        ({"Category": "a\x00b"}, "CONTROL_CHARS", "Category"),
        ({"Id": "nope"}, "INVALID_ID_FORMAT", "Id"),
    ],
)
def test_rejections(change, code, field):
    result = validate_record({**GOOD, **change}, IdPolicy())
    assert isinstance(result, Rejection)
    assert (result.code, result.field) == (code, field)


@pytest.mark.parametrize("missing", ["Id", "SellerName", "Name"])
def test_missing_required_field(missing):
    record = {k: v for k, v in GOOD.items() if k != missing}
    result = validate_record(record, IdPolicy())
    assert isinstance(result, Rejection)
    assert (result.code, result.field) == ("MISSING_FIELD", missing)


def test_non_object_record():
    result = validate_record(["not", "a", "dict"], IdPolicy())
    assert isinstance(result, Rejection) and result.code == "BAD_TYPE"


def test_optional_fields_may_be_absent():
    record = {"Id": GOOD["Id"], "SellerName": "S", "Name": "N"}
    result = validate_record(record, IdPolicy())
    assert isinstance(result, ValidRecord)
    assert result.brand is None and result.category is None


def test_display_clean():
    assert display_clean("  a \n b\t c  ") == "a b c"
    assert display_clean("Café") == "Café"  # NFC
    assert optional_clean(None) is None
    assert optional_clean("   ") is None


@pytest.mark.parametrize(("mode", "accepted"), [("lenient", 266), ("strict", 61)])
def test_sample_file_counts(mode, accepted):
    data = json.loads((Path(__file__).parents[2] / "data" / "ProductEntry.json").read_text())
    results = Counter(
        "ok" if isinstance(validate_record(r, IdPolicy(mode=mode)), ValidRecord) else "rejected"
        for r in data
    )
    assert results["ok"] == accepted
    assert results["rejected"] == len(data) - accepted
