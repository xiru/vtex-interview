"""Display-value cleaning.

Only cosmetic cleaning happens here. Normalized *keys* (used for uniqueness and lookups) are
computed exclusively by the database function ``catalog.name_key`` so there is one algorithm.
"""

from __future__ import annotations

import re
import unicodedata

_WHITESPACE = re.compile(r"\s+")


def display_clean(value: str) -> str:
    """NFC-normalize, collapse whitespace runs to one space and trim."""
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFC", value)).strip()


def optional_clean(value: str | None) -> str | None:
    """Like :func:`display_clean`, but empty results become ``None``."""
    if value is None:
        return None
    cleaned = display_clean(value)
    return cleaned or None
