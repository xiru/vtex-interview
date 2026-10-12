"""Settings parsing."""

from datetime import UTC, datetime, timedelta

import pytest

from catalog.config import (
    DEFAULT_DATABASE_URL,
    IdPolicy,
    Settings,
    SettingsError,
    parse_bool,
    parse_duration,
    parse_versions,
)
from catalog.db import normalize_url
from catalog.logconfig import configure_logging, safe_repr


def test_defaults():
    settings = Settings.from_env({})
    assert settings.house_seller_name == "VTEX"
    assert settings.database_url == DEFAULT_DATABASE_URL
    assert settings.api_database_url == DEFAULT_DATABASE_URL
    assert settings.id_policy == IdPolicy()
    assert settings.allow_empty_catalog is False
    assert settings.cors_origins == ()


def test_from_env_values():
    settings = Settings.from_env(
        {
            "DATABASE_URL": "postgresql://o@db/catalog",
            "API_DATABASE_URL": "postgresql://r@db/catalog",
            "HOUSE_SELLER_NAME": " Marketplace ",
            "ID_VALIDATION_MODE": "STRICT",
            "ID_ALLOWED_VERSIONS": "7",
            "ID_V7_MIN_DATE": "2024-05-01",
            "ID_V7_MAX_SKEW": "30s",
            "ALLOW_EMPTY_CATALOG": "yes",
            "CORS_ORIGINS": "http://a, http://b",
            "LOG_LEVEL": "debug",
        }
    )
    assert settings.house_seller_name == "Marketplace"
    assert settings.api_database_url == "postgresql://r@db/catalog"
    assert settings.id_policy.mode == "strict"
    assert settings.id_policy.versions == frozenset({7})
    assert settings.id_policy.v7_min_date == datetime(2024, 5, 1, tzinfo=UTC)
    assert settings.id_policy.v7_max_skew == timedelta(seconds=30)
    assert settings.allow_empty_catalog is True
    assert settings.cors_origins == ("http://a", "http://b")
    assert settings.log_level == "DEBUG"


@pytest.mark.parametrize(
    "env",
    [
        {"ID_VALIDATION_MODE": "paranoid"},
        {"ID_ALLOWED_VERSIONS": "4,9"},
        {"ID_ALLOWED_VERSIONS": "four"},
        {"ID_ALLOWED_VERSIONS": ""},
        {"ID_V7_MAX_SKEW": "5 minutes"},
        {"ID_V7_MIN_DATE": "yesterday"},
        {"HOUSE_SELLER_NAME": "  "},
    ],
)
def test_invalid_values(env):
    with pytest.raises(SettingsError):
        Settings.from_env(env)


def test_parsers():
    assert parse_duration("2d") == timedelta(days=2)
    assert parse_duration(" 36h ") == timedelta(hours=36)
    assert parse_duration("1w") == timedelta(weeks=1)
    assert parse_versions("4, 7") == frozenset({4, 7})
    assert parse_bool("on") and not parse_bool("0")


def test_normalize_url():
    assert normalize_url("postgres://u@h/d") == "postgresql+psycopg://u@h/d"
    assert normalize_url("postgresql://u@h/d") == "postgresql+psycopg://u@h/d"
    assert normalize_url("postgresql+psycopg://u@h/d") == "postgresql+psycopg://u@h/d"


def test_safe_repr_and_logging(tmp_path):
    assert safe_repr("a\nb") == "'a\\nb'"
    assert len(safe_repr("x" * 500)) == 200
    log_file = tmp_path / "out.log"
    configure_logging("INFO", str(log_file))
    import logging

    logging.getLogger("catalog.test").info("hello")
    assert "hello" in log_file.read_text()
