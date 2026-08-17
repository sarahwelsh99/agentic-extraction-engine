"""Tests for population_selection.selector.

classify_text is a pure-Python mirror of the BigQuery MERGE's REGEXP_CONTAINS
scoring (same PII_CATEGORY_PATTERNS, ported from mosaic-glean-extraction's
prefilter.py), so the classification rule can be verified here without a
live BigQuery connection. run_regex_pass applies the RE2-safe variant of the
same patterns server-side; the real validation for the patterns themselves is
the backtest against real drive_files extraction ground truth (see
selector.py's module docstring), not these unit tests -- these just guard the
any-match rule and the RE2 rewrite.
"""
from population_selection.selector import classify_text, PII_CATEGORY_PATTERNS, _to_re2


def test_single_category_is_enough_no_name_required():
    result = classify_text("Please update account number 4485921 on file.")
    assert result["has_pii"] is True
    assert result["pii_signals"] == "FINANCIAL_ACCOUNT"


def test_name_alone_is_not_pii():
    result = classify_text("Employee Name\nJohn Smith\nJane Doe\nBob Lee")
    assert result["has_pii"] is False
    assert result["pii_signals"] == ""


def test_name_plus_email_is_not_pii():
    """Contact info alone (name + email/phone) is deliberately not notifiable
    here -- matches prefilter.py's scoping, not this module's own invention."""
    result = classify_text("Full Name: John Smith, Email: john@example.com, Phone: 555-123-4567")
    assert result["has_pii"] is False


def test_no_signals_at_all():
    result = classify_text("Quarterly revenue by region\nEast,100\nWest,200")
    assert result["pii_score"] == 0
    assert result["has_pii"] is False


def test_dob_keyword():
    result = classify_text("Date of Birth: 1990-04-12")
    assert result["has_pii"] is True
    assert "DOB" in result["pii_signals"]


def test_dob_non_english():
    result = classify_text("Fecha de nacimiento: 12/04/1990")
    assert result["has_pii"] is True
    assert "DOB" in result["pii_signals"]


def test_raw_ssn_pattern_counts_as_government_id_without_keyword():
    result = classify_text("123-45-6789")
    assert result["has_pii"] is True
    assert "GOVERNMENT_ID" in result["pii_signals"]


def test_us_style_address():
    result = classify_text("123 Main Street, Springfield, IL 62704")
    assert result["has_pii"] is True
    assert "ADDRESS" in result["pii_signals"]


def test_european_style_address():
    result = classify_text("Av. Diagonal, 211, 08018 Barcelona")
    assert result["has_pii"] is True
    assert "ADDRESS" in result["pii_signals"]


def test_personal_email_domain():
    result = classify_text("Contact: someone@gmail.com")
    assert result["has_pii"] is True
    assert "PERSONAL_EMAIL" in result["pii_signals"]


def test_corporate_email_domain_is_not_personal_email():
    result = classify_text("Contact: someone@telus.com")
    assert result["has_pii"] is False


def test_empty_body_text():
    result = classify_text("")
    assert result["pii_score"] == 0
    assert result["has_pii"] is False


def test_every_category_pattern_compiles_in_python_and_re2_variant():
    import re
    for name, pattern in PII_CATEGORY_PATTERNS.items():
        re.compile(pattern)
        re.compile(_to_re2(pattern))  # sanity check the RE2 rewrite is still valid Python re syntax


def test_to_re2_strips_lookahead():
    assert "(?=" not in _to_re2(PII_CATEGORY_PATTERNS["ADDRESS"])
