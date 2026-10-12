"""Rules that reject values looking like injection attempts (SQL, HTML/JS).

This is a *policy* layer: the real protections are bound parameters everywhere, a JSON-only
API and an escaping, script-free UI. Rules are deliberately conservative so legitimate
product data (``Levi's``, ``34"``, ``BLACK+DECKER``, ``Select Comfort``) passes.
"""

from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass

import nh3

from catalog.validation.result import Rejection

# Whitespace control characters are allowed: display cleaning turns them into spaces.
_ALLOWED_CONTROLS = {"\t", "\n", "\r"}

_SQL_PATTERNS = [
    re.compile(
        r";\s*(select|insert|update|delete|drop|alter|create|truncate|exec|execute|union"
        r"|grant|revoke|merge|call|declare|shutdown)\b"
    ),
    re.compile(r"--"),
    re.compile(r"/\*|\*/"),
    re.compile(r"'\s*(or|and)\s+'?\w+'?\s*(=|like)\s*'?\w+"),
    re.compile(r"\bunion\s+(all\s+)?select\b"),
    re.compile(r"\b(sleep|pg_sleep|benchmark)\s*\("),
    re.compile(r"\bwaitfor\s+delay\b"),
    re.compile(r"\bxp_cmdshell\b|\binformation_schema\b"),
]

_SCRIPT_PATTERNS = [
    re.compile(r"\b(javascript|vbscript)\s*:"),
    re.compile(r"\bdata\s*:\s*[a-z]+/[a-z0-9.+-]+\s*[;,]"),
    re.compile(
        r"\bon(load|error|click|dblclick|mouse\w*|focus\w*|blur|change|submit|key\w*|input"
        r"|abort|animation\w*|toggle|pointer\w*|touch\w*|wheel|drag\w*|copy|paste|cut"
        r"|begin|end|show|scroll|resize|unload|message)\s*="
    ),
]


@dataclass(frozen=True)
class Rule:
    code: str
    check: Callable[[str], str | None]  # returns a detail message when the value is rejected


def _control_chars(value: str) -> str | None:
    for ch in value:
        if ch in _ALLOWED_CONTROLS:
            continue
        category = unicodedata.category(ch)
        # Cc: C0/C1 controls (incl. NUL); Cf: format chars (bidi overrides, zero-width ...).
        if category in ("Cc", "Cf"):
            return f"control/format character U+{ord(ch):04X}"
    return None


def _contains_markup(value: str) -> bool:
    return html.unescape(nh3.clean(value, tags=set())) != value


def _markup(value: str) -> str | None:
    folded = unicodedata.normalize("NFKC", value)
    unescaped = html.unescape(folded)
    if _contains_markup(folded) or (unescaped != folded and _contains_markup(unescaped)):
        return "contains HTML markup"
    lowered = unescaped.lower()
    for pattern in _SCRIPT_PATTERNS:
        if pattern.search(lowered):
            return "contains a script URI or event handler"
    return None


def _sql_injection(value: str) -> str | None:
    lowered = unicodedata.normalize("NFKC", value).lower()
    for pattern in _SQL_PATTERNS:
        if pattern.search(lowered):
            return "matches a SQL injection signature"
    return None


DEFAULT_RULES: tuple[Rule, ...] = (
    Rule("CONTROL_CHARS", _control_chars),
    Rule("MARKUP", _markup),
    Rule("SQL_INJECTION", _sql_injection),
)


def check_value(value: str, field: str, rules: Iterable[Rule] = DEFAULT_RULES) -> Rejection | None:
    """Return the first rule violation for ``value`` or ``None`` when it is acceptable."""
    for rule in rules:
        detail = rule.check(value)
        if detail:
            return Rejection(rule.code, field, detail)
    return None
