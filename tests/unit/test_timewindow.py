from datetime import UTC, datetime, timedelta

import pytest

from catalog.timewindow import parse_moment

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2d", NOW - timedelta(days=2)),
        ("36h", NOW - timedelta(hours=36)),
        ("90m", NOW - timedelta(minutes=90)),
        ("2026-10-10T00:00Z", datetime(2026, 10, 10, tzinfo=UTC)),
        ("2026-10-10", datetime(2026, 10, 10, tzinfo=UTC)),
        ("2026-10-10T03:00:00-03:00", datetime(2026, 10, 10, 6, tzinfo=UTC)),
    ],
)
def test_parse_moment(value, expected):
    assert parse_moment(value, NOW) == expected


def test_invalid_moment():
    with pytest.raises(ValueError, match="invalid time"):
        parse_moment("last tuesday", NOW)


def test_default_now_is_used():
    assert parse_moment("1h") < datetime.now(UTC)
