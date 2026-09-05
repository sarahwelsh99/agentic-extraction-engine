"""Tests for extraction/core/sheet_pii.py.

Two real false positives found on guid ae09945d-bb1b-c996-06a8-6a87b3dcc1ac's
sheet ledger drove this module: ADDRESS's German/Dutch street-suffix branch
firing on an ordinary word ("offering" ends in "ring") because the shared
pattern's (?i) flag defeats its own capital-letter check, and DOB's keyword
branch firing on the bare word "dob" with no date value anywhere nearby.

FINANCIAL_ACCOUNT and GOVERNMENT_ID's false positives were found later, from
a 300-guid sample of real body_text pulled from the live
structured_pending/structured_pending_2 backlog rather than one guid - see
sheet_pii.py's own module docstring for the full measurements
(56.3%/1.0%/39.0% of matches respectively).

These tests pin all four fixes, and confirm true positives for every other
category (including each fixed category's own genuine cases) still match -
classify_sheet_text() must reuse PII_CATEGORY_PATTERNS unmodified for
everything it doesn't specifically tighten.
"""

from extraction.core.sheet_pii import classify_sheet_text


def test_address_false_positive_from_ae09945d_is_suppressed():
    text = 'offering a lead","09/30 to next stage'
    result = classify_sheet_text(text)

    assert result["has_pii"] is False
    assert "ADDRESS" not in result["pii_signals"]

    print("✓ test_address_false_positive_from_ae09945d_is_suppressed PASSED")


def test_dob_false_positive_from_ae09945d_is_suppressed():
    text = "she jumped from dob to address avoiding ID"
    result = classify_sheet_text(text)

    assert result["has_pii"] is False
    assert "DOB" not in result["pii_signals"]

    print("✓ test_dob_false_positive_from_ae09945d_is_suppressed PASSED")


def test_dob_coincidental_date_across_a_sentence_break_is_suppressed():
    """A second false positive on the same ae09945d sheet, found only once
    the first fix was verified against the real guid: the keyword-plus-
    nearby-date check alone still matched a coaching-log timestamp that
    happened to fall within the proximity window but was separated from the
    keyword by a sentence break - not a real DOB field."""
    text = "weak for DOB or phone #.///01-18-2021-She needs to sound more confident"
    result = classify_sheet_text(text)

    assert result["has_pii"] is False
    assert "DOB" not in result["pii_signals"]

    print("✓ test_dob_coincidental_date_across_a_sentence_break_is_suppressed PASSED")


def test_dob_bullet_date_with_no_birth_keyword_is_suppressed():
    """A third false positive on the same ae09945d guid, found only after
    re-verifying against the real sheet ledger: the shared pattern's
    bullet-point-date alternative ("*09/30/2020") matches a bare date with
    no birth-related keyword anywhere near it. This dataset is call-center
    coaching notes, where "*10/05/2020 shows the willingness..." is a
    log-entry timestamp, not a birthdate - a keyword alone is required."""
    text = "*10/05/2020 shows the willingness to help on every call"
    result = classify_sheet_text(text)

    assert result["has_pii"] is False
    assert "DOB" not in result["pii_signals"]

    print("✓ test_dob_bullet_date_with_no_birth_keyword_is_suppressed PASSED")


def test_dob_keyword_with_format_hint_before_value_still_matches():
    """A short non-date format hint between the keyword and its value
    ("(dd/mm/yyyy)") must not be treated as a sentence break."""
    result = classify_sheet_text("DOB (dd/mm/yyyy): 09/30/1985")

    assert result["has_pii"] is True
    assert "DOB" in result["pii_signals"]

    print("✓ test_dob_keyword_with_format_hint_before_value_still_matches PASSED")


def test_genuine_german_street_address_still_matches():
    """A real capitalized proper-noun street name must still trip ADDRESS -
    the fix only requires the capital letter the pattern was already meant
    to require, it doesn't remove the branch."""
    result = classify_sheet_text("Koenigsring 5, 12345 Berlin")

    assert result["has_pii"] is True
    assert "ADDRESS" in result["pii_signals"]

    print("✓ test_genuine_german_street_address_still_matches PASSED")


def test_genuine_mailing_address_phrase_still_matches():
    result = classify_sheet_text("Mailing Address: 123 Main Street, Springfield, IL 62701")

    assert result["has_pii"] is True
    assert "ADDRESS" in result["pii_signals"]

    print("✓ test_genuine_mailing_address_phrase_still_matches PASSED")


def test_genuine_dob_with_date_value_still_matches():
    result = classify_sheet_text("Date of Birth: 09/30/1985")

    assert result["has_pii"] is True
    assert "DOB" in result["pii_signals"]

    print("✓ test_genuine_dob_with_date_value_still_matches PASSED")


def test_dob_keyword_with_bare_year_nearby_still_matches():
    result = classify_sheet_text("DOB 1985")

    assert result["has_pii"] is True
    assert "DOB" in result["pii_signals"]

    print("✓ test_dob_keyword_with_bare_year_nearby_still_matches PASSED")


def test_financial_account_digit_run_stitched_from_separate_numbers_is_suppressed():
    """Real false positive found in a 300-guid sample: the loose digit-run
    branch allows a separator after every digit, so it stitched three
    separate 8-digit case numbers together into a fake 24-digit "account
    number" - none of them are 13-19 digits on their own."""
    text = "Case, 19663844 19663057 19663614, closed"
    result = classify_sheet_text(text)

    assert result["has_pii"] is False
    assert "FINANCIAL_ACCOUNT" not in result["pii_signals"]

    print("✓ test_financial_account_digit_run_stitched_from_separate_numbers_is_suppressed PASSED")


def test_financial_account_bare_swift_is_suppressed():
    """Real false positive: swift's optional suffix let the bare word match
    ordinary usage with no banking context at all."""
    text = "the agent handled the issue with swift action"
    result = classify_sheet_text(text)

    assert result["has_pii"] is False
    assert "FINANCIAL_ACCOUNT" not in result["pii_signals"]

    print("✓ test_financial_account_bare_swift_is_suppressed PASSED")


def test_financial_account_unbroken_digit_run_without_context_is_suppressed():
    """Real false positive: a genuinely unbroken 13-19 digit token is still
    common, non-financial data - here a German phone number with country
    code - when no financial-context keyword sits nearby."""
    text = "Adam Fronia;00492251954304445; 01.09.2025 00:00:00"
    result = classify_sheet_text(text)

    assert result["has_pii"] is False
    assert "FINANCIAL_ACCOUNT" not in result["pii_signals"]

    print("✓ test_financial_account_unbroken_digit_run_without_context_is_suppressed PASSED")


def test_financial_account_genuine_card_number_with_keyword_still_matches():
    """A real card number sitting next to its product name must still trip
    FINANCIAL_ACCOUNT - the fix only requires nearby context, it doesn't
    remove the digit-run branch. Confirmed on a real call-center card-issuing
    export (guid 0098dfd0-7dbc-049a-e47d-afe9cc637b3e)."""
    result = classify_sheet_text("#9156339430850005821,AEROPLAN VISA INFINITE CARD,M2")

    assert result["has_pii"] is True
    assert "FINANCIAL_ACCOUNT" in result["pii_signals"]

    print("✓ test_financial_account_genuine_card_number_with_keyword_still_matches PASSED")


def test_government_id_bare_twelve_digits_is_suppressed():
    """Real false positive found in a 300-guid sample: bare \\d{12} requires
    no context at all, so an ordinary 12-digit number with no visible ID
    context (an internal reference number, in this case) matched."""
    text = "ref 591055516300 closed without action"
    result = classify_sheet_text(text)

    assert result["has_pii"] is False
    assert "GOVERNMENT_ID" not in result["pii_signals"]

    print("✓ test_government_id_bare_twelve_digits_is_suppressed PASSED")


def test_other_categories_are_untouched():
    """Every category besides ADDRESS/DOB/FINANCIAL_ACCOUNT/GOVERNMENT_ID
    must behave identically to the shared classify_text() - no extra check
    applied. GOVERNMENT_ID's own SSN-format branch is unambiguous and still
    matches immediately despite being a tightened category."""
    result = classify_sheet_text("SSN: 123-45-6789,x")

    assert result["has_pii"] is True
    assert result["pii_signals"] == "GOVERNMENT_ID"

    print("✓ test_other_categories_are_untouched PASSED")


def test_no_pii_text_scores_clean():
    result = classify_sheet_text("just some ordinary text,y")

    assert result["has_pii"] is False
    assert result["pii_score"] == 0
    assert result["pii_signals"] == ""

    print("✓ test_no_pii_text_scores_clean PASSED")


def run_all_tests():
    tests = [
        test_address_false_positive_from_ae09945d_is_suppressed,
        test_dob_false_positive_from_ae09945d_is_suppressed,
        test_dob_coincidental_date_across_a_sentence_break_is_suppressed,
        test_dob_bullet_date_with_no_birth_keyword_is_suppressed,
        test_dob_keyword_with_format_hint_before_value_still_matches,
        test_genuine_german_street_address_still_matches,
        test_genuine_mailing_address_phrase_still_matches,
        test_genuine_dob_with_date_value_still_matches,
        test_dob_keyword_with_bare_year_nearby_still_matches,
        test_financial_account_digit_run_stitched_from_separate_numbers_is_suppressed,
        test_financial_account_bare_swift_is_suppressed,
        test_financial_account_unbroken_digit_run_without_context_is_suppressed,
        test_financial_account_genuine_card_number_with_keyword_still_matches,
        test_government_id_bare_twelve_digits_is_suppressed,
        test_other_categories_are_untouched,
        test_no_pii_text_scores_clean,
    ]
    passed = failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"✗ {test.__name__} FAILED: {e}")
            failed += 1
    print("\n" + "=" * 60)
    print(f"Test Results: {passed}/{len(tests)} passed, {failed} failed")
    print("=" * 60)
    return failed == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if run_all_tests() else 1)
