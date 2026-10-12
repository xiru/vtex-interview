"""UUID policy for seller product ids (lenient / strict, allowed versions, v7 timestamps)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from catalog.config import IdPolicy
from catalog.validation.identifiers import v7_timestamp, validate_uuid
from catalog.validation.result import Rejection

LENIENT = IdPolicy()
STRICT = IdPolicy(mode="strict")
NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


def make_v7(moment: datetime) -> str:
    millis = int(moment.timestamp() * 1000)
    raw = f"{millis:012x}" + "7abc" + "8def" + "0123456789ab"
    return str(uuid.UUID(raw))


def code(result):
    return result.code if isinstance(result, Rejection) else "ok"


@pytest.mark.parametrize(
    ("value", "lenient", "strict"),
    [
        (str(uuid.uuid4()), "ok", "ok"),
        (str(uuid.uuid7()), "ok", "ok"),
        # sample-file style: version 4 but non-RFC variant nibble
        ("66778899-0011-4223-3445-567788990234", "ok", "INVALID_ID_VARIANT"),
        ("A1B2C3D4-E5F6-4A5B-8C9D-0E1F2A3B4C5D", "ok", "ok"),
        ("ddddeee-ffff-4000-1111-222233334444", "INVALID_ID_FORMAT", "INVALID_ID_FORMAT"),
        ("09835342345-4678-9abc-def012345678", "INVALID_ID_FORMAT", "INVALID_ID_FORMAT"),
        ("uddd0000-eeee-4111-ffff-aaaa22223333", "INVALID_ID_FORMAT", "INVALID_ID_FORMAT"),
        ("{a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d}", "INVALID_ID_FORMAT", "INVALID_ID_FORMAT"),
        (
            "urn:uuid:a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d",
            "INVALID_ID_FORMAT",
            "INVALID_ID_FORMAT",
        ),
        ("a1b2c3d4e5f64a5b8c9d0e1f2a3b4c5d", "INVALID_ID_FORMAT", "INVALID_ID_FORMAT"),
        (str(uuid.uuid1()), "INVALID_ID_VERSION", "INVALID_ID_VERSION"),
        ("00000000-0000-0000-0000-000000000000", "INVALID_ID_VERSION", "INVALID_ID_VERSION"),
        (12345, "INVALID_ID_FORMAT", "INVALID_ID_FORMAT"),
    ],
)
def test_modes(value, lenient, strict):
    assert code(validate_uuid(value, LENIENT, NOW)) == lenient
    assert code(validate_uuid(value, STRICT, datetime.now(UTC))) == strict


def test_result_is_lowercase_uuid():
    result = validate_uuid("A1B2C3D4-E5F6-4A5B-8C9D-0E1F2A3B4C5D", LENIENT)
    assert result == uuid.UUID("a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d")


@pytest.mark.parametrize(
    "value", ["00000000-0000-0000-0000-000000000000", "ffffffff-ffff-ffff-ffff-ffffffffffff"]
)
def test_nil_and_max_are_never_valid(value):
    every_version = IdPolicy(mode="strict", versions=frozenset(range(1, 9)))
    for policy in (LENIENT, every_version):
        assert code(validate_uuid(value, policy)) == "INVALID_ID_VERSION"


def test_v7_timestamp_window_in_strict_mode():
    ok = make_v7(NOW - timedelta(days=1))
    future = make_v7(NOW + timedelta(hours=1))
    ancient = make_v7(datetime(2010, 1, 1, tzinfo=UTC))
    assert code(validate_uuid(ok, STRICT, NOW)) == "ok"
    assert code(validate_uuid(future, STRICT, NOW)) == "INVALID_ID_V7_TIMESTAMP"
    assert code(validate_uuid(ancient, STRICT, NOW)) == "INVALID_ID_V7_TIMESTAMP"
    # lenient mode does not look at the timestamp
    assert code(validate_uuid(future, LENIENT, NOW)) == "ok"


def test_v7_timestamp_extraction():
    moment = datetime(2026, 1, 2, 3, 4, 5, 678000, tzinfo=UTC)
    assert v7_timestamp(make_v7(moment)) == moment


def test_only_v7_policy():
    only_v7 = IdPolicy(mode="strict", versions=frozenset({7}))
    assert code(validate_uuid(str(uuid.uuid4()), only_v7)) == "INVALID_ID_VERSION"
    assert code(validate_uuid(str(uuid.uuid7()), only_v7)) == "ok"


def test_describe():
    assert LENIENT.describe() == "lenient;versions=4,7"
    assert IdPolicy(mode="strict", versions=frozenset({7})).describe() == "strict;versions=7"
