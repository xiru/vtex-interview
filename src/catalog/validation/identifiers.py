"""Seller product id (UUID) validation with a configurable strictness policy.

``lenient`` (default): canonical 8-4-4-4-12 hex and an allowed version nibble.
``strict``: additionally RFC 9562 variant bits and, for v7, a plausible embedded timestamp.

The Nil (all 0) and Max (all f) UUIDs carry version nibbles 0 and 15, which are never in the
allowed set (1-8), so both modes reject them with ``INVALID_ID_VERSION``. The version nibble
is read from the string because Python's ``uuid.UUID.version`` is ``None`` whenever the
variant is not RFC 4122/9562.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

from catalog.config import IdPolicy
from catalog.validation.result import Rejection

CANONICAL_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def v7_timestamp(value: str) -> datetime:
    """Creation time embedded in a canonical UUIDv7 string (first 48 bits, Unix ms)."""
    millis = int(value.replace("-", "")[:12], 16)
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=millis)


def validate_uuid(
    value: object, policy: IdPolicy, now: datetime | None = None, field: str = "Id"
) -> uuid.UUID | Rejection:
    if not isinstance(value, str) or not CANONICAL_UUID.match(value):
        return Rejection("INVALID_ID_FORMAT", field, "not a canonical 8-4-4-4-12 hex UUID")
    text = value.lower()
    version = int(text[14], 16)
    if version not in policy.versions:
        allowed = ",".join(str(v) for v in sorted(policy.versions))
        return Rejection("INVALID_ID_VERSION", field, f"version {version} not in {{{allowed}}}")
    if policy.mode == "strict":
        if text[19] not in "89ab":
            return Rejection("INVALID_ID_VARIANT", field, "variant bits are not RFC 9562 (10xx)")
        if version == 7:
            moment = v7_timestamp(text)
            now = now or datetime.now(UTC)
            if moment < policy.v7_min_date or moment > now + policy.v7_max_skew:
                return Rejection(
                    "INVALID_ID_V7_TIMESTAMP", field, f"embedded time {moment.isoformat()}"
                )
    return uuid.UUID(text)
