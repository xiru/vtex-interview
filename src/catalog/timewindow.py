"""Parsing of --since/--until values: relative (``2d``, ``36h``, ``90m``) or ISO timestamps."""

from __future__ import annotations

from datetime import UTC, datetime

from catalog.config import SettingsError, parse_duration


def parse_moment(value: str, now: datetime | None = None) -> datetime:
    """Return an aware datetime. Relative values count back from ``now``."""
    now = now or datetime.now(UTC)
    text = value.strip()
    try:
        return now - parse_duration(text)
    except SettingsError:
        pass
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"invalid time {value!r}: use a duration like 2d / 36h / 90m or an ISO timestamp"
        ) from exc
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)
