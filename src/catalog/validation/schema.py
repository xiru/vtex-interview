"""Structural validation of a seller record (types, required fields, lengths)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError

from catalog.validation.result import Rejection

MAX_NAME = 300
MAX_OTHER = 120


class SellerRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    Id: StrictStr = Field(max_length=64)
    SellerName: StrictStr = Field(max_length=MAX_OTHER)
    Name: StrictStr = Field(max_length=MAX_NAME)
    Brand: StrictStr | None = Field(default=None, max_length=MAX_OTHER)
    Category: StrictStr | None = Field(default=None, max_length=MAX_OTHER)


def parse_record(raw: object) -> SellerRecord | Rejection:
    if not isinstance(raw, dict):
        return Rejection("BAD_TYPE", None, "record is not a JSON object")
    try:
        return SellerRecord.model_validate(raw)
    except ValidationError as exc:
        error = exc.errors()[0]
        field = ".".join(str(part) for part in error["loc"]) or None
        kind = error["type"]
        if kind == "missing":
            return Rejection("MISSING_FIELD", field, "required field is missing")
        if kind == "string_too_long":
            return Rejection("TOO_LONG", field, error["msg"])
        return Rejection("BAD_TYPE", field, error["msg"])
