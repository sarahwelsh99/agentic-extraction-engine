"""Tests for extraction/core/document_type.py.

The taxonomy/skip-set invariants are pure (no I/O). classify_document_type
itself hits the real vLLM server, following this repo's convention for LLM
calls (CLAUDE.md: "most tool tests call the real server rather than mocking
it"). Requires:
    curl http://localhost:8000/v1/models
"""

import asyncio

from extraction.core.document_type import (
    DOCUMENT_TYPE_CATEGORIES, SKIP_DOCUMENT_TYPES, classify_document_type,
)
from extraction.core.llm_service import LocalLLMClient


def test_other_is_never_in_the_skip_set():
    assert "other" in DOCUMENT_TYPE_CATEGORIES
    assert "other" not in SKIP_DOCUMENT_TYPES

    print("✓ test_other_is_never_in_the_skip_set PASSED")


def test_tabular_export_is_never_in_the_skip_set():
    """The whole point of the 2026-08-19 taxonomy fix: a genuine data
    export must have a category the model can correctly pick, and that
    category must never be treated as skippable."""
    assert "tabular data export or report" in DOCUMENT_TYPE_CATEGORIES
    assert "tabular data export or report" not in SKIP_DOCUMENT_TYPES

    print("✓ test_tabular_export_is_never_in_the_skip_set PASSED")


def test_skip_set_is_every_other_category():
    assert SKIP_DOCUMENT_TYPES == (
        set(DOCUMENT_TYPE_CATEGORIES) - {"other", "tabular data export or report"}
    )
    assert len(DOCUMENT_TYPE_CATEGORIES) == 11

    print("✓ test_skip_set_is_every_other_category PASSED")


def test_empty_body_text_returns_none_without_a_call():
    result = asyncio.run(classify_document_type(LocalLLMClient(), ""))
    assert result is None

    print("✓ test_empty_body_text_returns_none_without_a_call PASSED")


def test_classifies_a_real_book_excerpt():
    """A real end-to-end check: an obvious book excerpt should classify into
    SOME member of the taxonomy - not necessarily the exact expected label
    (LLM judgment can vary), but a valid, non-None category."""
    sample = (
        "Chapter 3: The Origins of Modern Finance\n\n"
        "In the preceding chapter, we examined the foundational principles "
        "of corporate valuation. This chapter turns to the historical "
        "development of capital markets, tracing their evolution from "
        "medieval merchant guilds to the modern stock exchange. As we will "
        "see throughout this book, the interplay between regulation and "
        "innovation has shaped every major financial instrument in use "
        "today.\n\n"
        "3.1 The Merchant Guilds\n"
        "Long before the first stock exchange opened its doors, merchant "
        "guilds across Europe developed sophisticated systems of credit "
        "and risk-sharing..."
    )
    result = asyncio.run(classify_document_type(LocalLLMClient(), sample))
    assert result in DOCUMENT_TYPE_CATEGORIES, result

    print(f"✓ test_classifies_a_real_book_excerpt PASSED (classified: {result})")


def test_classifies_a_real_csv_as_not_skippable():
    """A genuine tabular export must not classify into any skip-listed
    genre - the whole point of this check is to leave real structured data
    alone."""
    sample = (
        "employee_id,first_name,last_name,department,hire_date\n"
        "10001,John,Smith,Engineering,2019-03-14\n"
        "10002,Jane,Doe,Marketing,2020-07-01\n"
        "10003,Alex,Nguyen,Finance,2018-11-23\n"
    )
    result = asyncio.run(classify_document_type(LocalLLMClient(), sample))
    assert result not in SKIP_DOCUMENT_TYPES, result

    print(f"✓ test_classifies_a_real_csv_as_not_skippable PASSED (classified: {result})")


def test_hr_shaped_csv_export_is_not_misclassified_as_hr_document():
    """Regression test for the 2026-08-19 false-positive batch: a real
    Workday-style HR export (standard field names, no real employee data)
    was misclassified 'hr or personnel document' (skip-listed) under the
    original mosaic taxonomy, because the column names' SUBJECT MATTER (HR)
    outweighed the document's actual FORM (a delimited table) with no
    correct category to pick instead. Must now land on 'tabular data export
    or report', not any skip-listed genre."""
    sample = (
        "Workday ID,Worker,Job Profile,Business Title,Job Code,"
        "Immediate Manager,Supervisory Organization,Cost Center - ID,"
        "Cost Center - Name,Functional Area,Division,Site,"
        "Original Hire Date,Hire Date,Years of Service\n"
        "W1001,Employee One,Software Engineer,Senior Engineer,ENG-01,"
        "Manager A,Engineering Org,CC-100,Engineering,Technology,"
        "Product,Toronto,2018-01-15,2018-01-15,7\n"
        "W1002,Employee Two,Product Manager,Senior PM,PM-02,"
        "Manager B,Product Org,CC-200,Product,Technology,"
        "Product,Vancouver,2020-06-01,2020-06-01,5\n"
    )
    result = asyncio.run(classify_document_type(LocalLLMClient(), sample))
    assert result not in SKIP_DOCUMENT_TYPES, result

    print(f"✓ test_hr_shaped_csv_export_is_not_misclassified_as_hr_document PASSED (classified: {result})")


def run_all_tests():
    tests = [
        test_other_is_never_in_the_skip_set,
        test_tabular_export_is_never_in_the_skip_set,
        test_skip_set_is_every_other_category,
        test_empty_body_text_returns_none_without_a_call,
        test_classifies_a_real_book_excerpt,
        test_classifies_a_real_csv_as_not_skippable,
        test_hr_shaped_csv_export_is_not_misclassified_as_hr_document,
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
