"""Result types shared by the validation modules."""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class Rejection:
    """Why a value or record was rejected. ``code`` is stable and documented."""

    code: str
    field: str | None = None
    detail: str = ""

    def __str__(self) -> str:
        where = f" [{self.field}]" if self.field else ""
        return f"{self.code}{where}: {self.detail}" if self.detail else f"{self.code}{where}"


@dataclass(frozen=True)
class ValidRecord:
    """A seller record that passed validation, with cleaned display values."""

    seller_product_id: uuid.UUID
    seller_name: str
    name: str
    brand: str | None
    category: str | None
