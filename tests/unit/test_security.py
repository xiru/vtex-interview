"""Security rules: malicious values are rejected, legitimate product data passes."""

import pytest

from catalog.validation.security import check_value

MALICIOUS = [
    ("TestBrand'; SELECT 1; --", "SQL_INJECTION"),
    ("x'; DROP TABLE product; --", "SQL_INJECTION"),
    ("name; delete from catalog.seller", "SQL_INJECTION"),
    ("' OR '1'='1", "SQL_INJECTION"),
    ("admin' or 1=1", "SQL_INJECTION"),
    ("1 UNION SELECT password FROM users", "SQL_INJECTION"),
    ("1 union all select 1", "SQL_INJECTION"),
    ("value -- comment", "SQL_INJECTION"),
    ("value /* comment */", "SQL_INJECTION"),
    ("pg_sleep(10)", "SQL_INJECTION"),
    ("benchmark(1000000, md5(1))", "SQL_INJECTION"),
    ("1; waitfor delay '0:0:5'", "SQL_INJECTION"),
    ("exec xp_cmdshell 'dir'", "SQL_INJECTION"),
    ("select * from information_schema.tables", "SQL_INJECTION"),
    ("ＳＥＬＥＣＴ ＵＮＩＯＮ ＳＥＬＥＣＴ", "SQL_INJECTION"),  # full-width look-alike
    ("<script>alert(1)</script>", "MARKUP"),
    ("<img src=x onerror=alert(1)>", "MARKUP"),
    ("Nice <b>bold</b> name", "MARKUP"),
    ("<!-- hidden -->name", "MARKUP"),
    ("&lt;script&gt;alert(1)&lt;/script&gt;", "MARKUP"),
    ("&#x3c;svg onload=alert(1)&#x3e;", "MARKUP"),
    ("javascript:alert(1)", "MARKUP"),
    ("VBScript:msgbox", "MARKUP"),
    ("data:text/html;base64,PHNjcmlwdD4=", "MARKUP"),
    ("x onerror=alert(1)", "MARKUP"),
    ("Name\x00", "CONTROL_CHARS"),
    ("Name\x07bell", "CONTROL_CHARS"),
    ("abc‮dcba", "CONTROL_CHARS"),  # right-to-left override
    ("zero​width", "CONTROL_CHARS"),
    ("⁦isolate⁩", "CONTROL_CHARS"),
]

LEGITIMATE = [
    "Levi's",
    'Monitor LG UltraWide 34"',
    "BLACK+DECKER",
    "5.11",
    "Park & Sun Sports",
    "Dock & Bay",
    "Rock 'n' Roll",
    "Select Comfort",
    "Drop Ship Co",
    "C++ Primer",
    "AT&T",
    "WD-40",
    "Câmera Canon EOS R6",
    "Ørsted Straße",
    "東京 Tokyo",
    "a < b and c > d",
    "50% off: Data Science Kit",
    "One = 1 pack",
    "Union Square Tote",
    "Kids' Sleep Mask",
    "Tab\tand\nnewline",
    "Mother's Day: gift",
]


@pytest.mark.parametrize(("value", "code"), MALICIOUS)
def test_malicious_values_are_rejected(value, code):
    rejection = check_value(value, "Name")
    assert rejection is not None, value
    assert rejection.code == code
    assert rejection.field == "Name"


@pytest.mark.parametrize("value", LEGITIMATE)
def test_legitimate_values_pass(value):
    assert check_value(value, "Name") is None


def test_rejection_str_mentions_code_and_field():
    rejection = check_value("<b>x</b>", "Brand")
    assert str(rejection).startswith("MARKUP [Brand]")
