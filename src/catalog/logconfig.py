"""Logging configuration (standard library ``logging``)."""

from __future__ import annotations

import logging
import sys

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging(level: str = "INFO", log_file: str | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(level=level.upper(), format=LOG_FORMAT, handlers=handlers, force=True)
    # Alembic is chatty at INFO ("Context impl ..."); keep it for DEBUG runs only.
    if logging.getLogger().level > logging.DEBUG:
        logging.getLogger("alembic").setLevel(logging.WARNING)


def safe_repr(value: object, limit: int = 200) -> str:
    """repr() a value for logging: no raw newlines/control chars, bounded length."""
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."
