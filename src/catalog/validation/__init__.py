"""Validation of seller records before they reach the database.

``validate_record`` returns a :class:`ValidRecord` or the first :class:`Rejection` found.
The ``EMPTY_KEY`` rule (a name without latin letters/digits) is checked by the importer
through the database key function so it matches the database CHECK exactly.
"""

from __future__ import annotations

from datetime import datetime

from catalog.config import IdPolicy
from catalog.validation.identifiers import validate_uuid
from catalog.validation.result import Rejection, ValidRecord
from catalog.validation.schema import parse_record
from catalog.validation.security import check_value
from catalog.validation.text import display_clean, optional_clean

__all__ = ["Rejection", "ValidRecord", "check_text_fields", "validate_record"]


def check_text_fields(fields: dict[str, str | None]) -> Rejection | None:
    """Run the security rules on every non-null text field."""
    for field, value in fields.items():
        if value is None:
            continue
        rejection = check_value(value, field)
        if rejection:
            return rejection
    return None


def validate_record(
    raw: object, policy: IdPolicy, now: datetime | None = None
) -> ValidRecord | Rejection:
    record = parse_record(raw)
    if isinstance(record, Rejection):
        return record
    rejection = check_text_fields(
        {
            "SellerName": record.SellerName,
            "Name": record.Name,
            "Brand": record.Brand,
            "Category": record.Category,
        }
    )
    if rejection:
        return rejection
    seller_name = display_clean(record.SellerName)
    name = display_clean(record.Name)
    if not seller_name:
        return Rejection("MISSING_FIELD", "SellerName", "empty after trimming")
    if not name:
        return Rejection("MISSING_FIELD", "Name", "empty after trimming")
    identifier = validate_uuid(record.Id, policy, now)
    if isinstance(identifier, Rejection):
        return identifier
    return ValidRecord(
        seller_product_id=identifier,
        seller_name=seller_name,
        name=name,
        brand=optional_clean(record.Brand),
        category=optional_clean(record.Category),
    )
