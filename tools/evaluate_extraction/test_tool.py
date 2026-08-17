"""Tests for evaluate_extraction (Tool 5).

Tool 5 answers one question — did the extraction work? — by comparing what came
out against the source. These tests pin that answer, because it decides whether
a document reaches BigQuery.
"""

import json
from tools.evaluate_extraction.tool import EvaluateExtractionTool

REPORT = {"modal_field_count": 3, "header_names": ["id", "name", "email"]}


def _evaluate(rows, rows_in=None, attempt=1, status="success", error=None):
    execution = {
        "status": status,
        "extracted_rows": rows,
        "total_rows": rows_in if rows_in is not None else len(rows),
    }
    if error:
        execution["error"] = error
    return json.loads(EvaluateExtractionTool()({
        "guid": "g",
        "execution_result": execution,
        "metadata_report": REPORT,
        "attempt": attempt,
    }))


def _rows(valid, invalid=0):
    rows = [{"id": i, "name": "n", "email": "e@x.com", "_valid": True,
             "_row_number": i} for i in range(valid)]
    rows += [{"id": None, "_valid": False, "_errors": ["could not read field 2"],
              "_row_number": 900 + i} for i in range(invalid)]
    return rows


def test_clean_extraction_passes():
    """Every source row accounted for and parsed cleanly."""
    r = _evaluate(_rows(50), rows_in=50)

    assert r["extraction_passed"] is True
    assert r["failure_reason"] is None
    assert r["should_retry"] is False
    assert r["evaluation"]["source_coverage"] == 1.0

    print("✓ test_clean_extraction_passes PASSED")


def test_a_few_bad_rows_still_passes():
    """Real documents contain some bad rows; that is not a failed extraction."""
    r = _evaluate(_rows(48, 2), rows_in=50)

    assert r["extraction_passed"] is True, r["failure_reason"]

    print("✓ test_a_few_bad_rows_still_passes PASSED")


def test_mostly_unparsed_rows_fails():
    """If most returned rows did not parse, the script did not work."""
    r = _evaluate(_rows(10, 40), rows_in=50)

    assert r["extraction_passed"] is False
    assert "parsed cleanly" in r["failure_reason"]
    # The reason carries a real error so the next attempt can act on it
    assert "could not read field 2" in r["failure_reason"]

    print("✓ test_mostly_unparsed_rows_fails PASSED")


def test_dropped_rows_fails_even_when_those_returned_are_perfect():
    """Two perfect rows out of a thousand is not a working extraction."""
    r = _evaluate(_rows(2), rows_in=1000)

    assert r["extraction_passed"] is False
    assert "from 1000 source rows" in r["failure_reason"]

    print("✓ test_dropped_rows_fails_even_when_those_returned_are_perfect PASSED")


def test_nothing_returned_from_a_populated_source_fails():
    r = _evaluate([], rows_in=25)

    assert r["extraction_passed"] is False
    assert "returned none" in r["failure_reason"]

    print("✓ test_nothing_returned_from_a_populated_source_fails PASSED")


def test_empty_source_does_not_pass_but_is_named_as_such():
    """Nothing to extract is distinguished from a broken script."""
    r = _evaluate([], rows_in=0)

    assert r["extraction_passed"] is False
    assert "no data rows" in r["failure_reason"]

    print("✓ test_empty_source_does_not_pass_but_is_named_as_such PASSED")


def test_script_that_would_not_run_is_retried_with_the_error():
    r = _evaluate([], status="error", error="NameError: name 'person' is not defined")

    assert r["extraction_passed"] is False
    assert r["should_retry"] is True
    assert "NameError" in r["failure_reason"]

    print("✓ test_script_that_would_not_run_is_retried_with_the_error PASSED")


def test_no_retry_on_the_final_attempt():
    r = _evaluate(_rows(1, 49), rows_in=50,
                  attempt=EvaluateExtractionTool.MAX_ATTEMPTS)

    assert r["extraction_passed"] is False
    assert r["should_retry"] is False

    print("✓ test_no_retry_on_the_final_attempt PASSED")


def test_short_column_delivery_is_retried():
    """Fewer columns than specified means values are landing under wrong names.

    Real case: a staff roster whose table is 26 columns wide. The script returned
    four values per row, and because the caller pairs position to name, those
    four were labelled with the header's first four names — Team, Skill,
    Aze_User, Name Surname — holding a user id and a duration. Nothing else
    catches it: the rows parse, and the row count matches.
    """
    rows = [
        {"c0": "v", "c1": "v", "c2": "v", "c3": "v", "_valid": True, "_row_number": i}
        for i in range(20)
    ]
    r = json.loads(EvaluateExtractionTool()({
        "guid": "g",
        "execution_result": {"status": "success", "extracted_rows": rows, "total_rows": 20},
        "metadata_report": {"modal_field_count": 26, "header_field_count": 43},
        "attempt": 1,
    }))

    assert r["extraction_passed"] is False
    assert r["should_retry"] is True
    assert "wrong column names" in r["failure_reason"]
    # the reason tells the next attempt exactly what to do
    assert "26 values per row" in r["failure_reason"]
    assert r["evaluation"]["column_delivery"] == round(4 / 26, 3)

    print("\u2713 test_short_column_delivery_is_retried PASSED")


def test_slightly_short_delivery_still_passes():
    """A column or two short is normal trimming, not a mislabelling."""
    rows = [
        {f"c{i}": "v" for i in range(25)} | {"_valid": True, "_row_number": n}
        for n in range(20)
    ]
    r = json.loads(EvaluateExtractionTool()({
        "guid": "g",
        "execution_result": {"status": "success", "extracted_rows": rows, "total_rows": 20},
        "metadata_report": {"modal_field_count": 26},
        "attempt": 1,
    }))

    assert r["extraction_passed"] is True, r["failure_reason"]

    print("\u2713 test_slightly_short_delivery_still_passes PASSED")


def test_missing_execution_result_is_an_error():
    r = json.loads(EvaluateExtractionTool()({"guid": "g", "metadata_report": REPORT}))

    assert r["status"] == "error"
    assert "execution_result" in r["error"]

    print("✓ test_missing_execution_result_is_an_error PASSED")


def run_all_tests():
    tests = [
        test_clean_extraction_passes,
        test_a_few_bad_rows_still_passes,
        test_mostly_unparsed_rows_fails,
        test_dropped_rows_fails_even_when_those_returned_are_perfect,
        test_nothing_returned_from_a_populated_source_fails,
        test_empty_source_does_not_pass_but_is_named_as_such,
        test_script_that_would_not_run_is_retried_with_the_error,
        test_no_retry_on_the_final_attempt,
        test_short_column_delivery_is_retried,
        test_slightly_short_delivery_still_passes,
        test_missing_execution_result_is_an_error,
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
