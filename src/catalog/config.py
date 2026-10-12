"""Application settings, read from environment variables.

Every setting has a sensible default so the CLI and the tests work out of the box; Docker
Compose passes the values from the top-level ``.env`` file.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import lru_cache

#: Name of the house seller (the marketplace itself), created with id ``HOUSE_SELLER_ID``.
DEFAULT_HOUSE_SELLER_NAME = "VTEX"
#: Id of the house seller. It owns the legacy catalog and is never deactivated.
HOUSE_SELLER_ID = 1

DEFAULT_DATABASE_URL = "postgresql+psycopg://catalog_owner:catalog_owner@localhost:5432/catalog"

ID_VALIDATION_MODES = ("lenient", "strict")

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$")
_DURATION_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


class SettingsError(ValueError):
    """Raised when an environment variable holds an invalid value."""


def parse_duration(text: str) -> timedelta:
    """Parse a short duration such as ``5m``, ``36h`` or ``2d``."""
    match = _DURATION_RE.match(text)
    if not match:
        raise SettingsError(f"invalid duration {text!r} (expected e.g. 30s, 5m, 36h, 2d, 1w)")
    amount, unit = match.groups()
    return timedelta(**{_DURATION_UNITS[unit]: int(amount)})


def parse_versions(text: str) -> frozenset[int]:
    """Parse a comma separated list of UUID versions (1-8)."""
    try:
        versions = frozenset(int(part) for part in text.split(",") if part.strip())
    except ValueError as exc:
        raise SettingsError(f"invalid UUID version list {text!r}") from exc
    if not versions or any(v < 1 or v > 8 for v in versions):
        raise SettingsError(f"UUID versions must be between 1 and 8, got {text!r}")
    return versions


def parse_bool(text: str) -> bool:
    return text.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class IdPolicy:
    """How strictly seller product ids (UUIDs) are validated. See DESIGN.md, UUID policy."""

    mode: str = "lenient"
    versions: frozenset[int] = frozenset({4, 7})
    v7_min_date: datetime = datetime(2020, 1, 1, tzinfo=UTC)
    v7_max_skew: timedelta = timedelta(minutes=5)

    def __post_init__(self) -> None:
        if self.mode not in ID_VALIDATION_MODES:
            raise SettingsError(f"ID validation mode must be one of {ID_VALIDATION_MODES}")

    def describe(self) -> str:
        """Short form stored with every import, e.g. ``lenient;versions=4,7``."""
        return f"{self.mode};versions={','.join(str(v) for v in sorted(self.versions))}"


@dataclass(frozen=True)
class Settings:
    database_url: str = DEFAULT_DATABASE_URL
    api_database_url: str = DEFAULT_DATABASE_URL
    house_seller_name: str = DEFAULT_HOUSE_SELLER_NAME
    id_policy: IdPolicy = field(default_factory=IdPolicy)
    allow_empty_catalog: bool = False
    cors_origins: tuple[str, ...] = ()
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        env = dict(os.environ) if env is None else env
        database_url = env.get("DATABASE_URL", DEFAULT_DATABASE_URL)
        min_date = env.get("ID_V7_MIN_DATE", "2020-01-01")
        try:
            v7_min = datetime.fromisoformat(min_date)
        except ValueError as exc:
            raise SettingsError(f"invalid ID_V7_MIN_DATE {min_date!r}") from exc
        if v7_min.tzinfo is None:
            v7_min = v7_min.replace(tzinfo=UTC)
        policy = IdPolicy(
            mode=env.get("ID_VALIDATION_MODE", "lenient").strip().lower(),
            versions=parse_versions(env.get("ID_ALLOWED_VERSIONS", "4,7")),
            v7_min_date=v7_min,
            v7_max_skew=parse_duration(env.get("ID_V7_MAX_SKEW", "5m")),
        )
        cors = tuple(o.strip() for o in env.get("CORS_ORIGINS", "").split(",") if o.strip())
        house = env.get("HOUSE_SELLER_NAME", DEFAULT_HOUSE_SELLER_NAME).strip()
        if not house:
            raise SettingsError("HOUSE_SELLER_NAME must not be empty")
        return cls(
            database_url=database_url,
            api_database_url=env.get("API_DATABASE_URL", database_url),
            house_seller_name=house,
            id_policy=policy,
            allow_empty_catalog=parse_bool(env.get("ALLOW_EMPTY_CATALOG", "0")),
            cors_origins=cors,
            log_level=env.get("LOG_LEVEL", "INFO").upper(),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
