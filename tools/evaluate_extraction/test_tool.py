"""Tests for evaluate_extraction (Tool 5).

Tool 5 answers one question — did the extraction work? — by comparing what came
out against the source. These tests pin that answer, because it decides whether
a document reaches delivery.
"""

import json
from tools.evaluate_extraction.tool import EvaluateExtractionTool

REPORT = {"modal_field_count": 3, "header_names": ["id", "name", "email"]}


def _evaluate(rows, rows_in=None, attempt=1, status="success", error=None,
              report=None):
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
        "metadata_report": report if report is not None else REPORT,
        "attempt": attempt,
    }))


def _rows(valid, invalid=0):
    rows = [{"id": i, "name": "n", "email": "e@x.com", "_valid": True,
             "_row_number": i} for i in range(valid)]
    rows += [{"id": None, "_valid": False, "_errors": ["could not read field 2"],
              "_row_number": 900 + i} for i in range(invalid)]
    return rows


def test_passing_cases():
    """Every source row accounted for and parsed cleanly - a few bad rows included."""
    clean = _evaluate(_rows(50), rows_in=50)
    assert clean["extraction_passed"] is True
    assert clean["failure_reason"] is None
    assert clean["should_retry"] is False
    assert clean["evaluation"]["source_coverage"] == 1.0

    mostly_clean = _evaluate(_rows(48, 2), rows_in=50)
    assert mostly_clean["extraction_passed"] is True, mostly_clean["failure_reason"]

    print("✓ test_passing_cases PASSED")


def test_quality_threshold_failures():
    """Too few valid rows, or too few rows returned at all, both fail."""
    mostly_unparsed = _evaluate(_rows(10, 40), rows_in=50)
    assert mostly_unparsed["extraction_passed"] is False
    assert "parsed cleanly" in mostly_unparsed["failure_reason"]
    assert "could not read field 2" in mostly_unparsed["failure_reason"]

    dropped_rows = _evaluate(_rows(2), rows_in=1000)
    assert dropped_rows["extraction_passed"] is False
    assert "from 1000 source rows" in dropped_rows["failure_reason"]

    print("✓ test_quality_threshold_failures PASSED")


def test_no_rows_cases_are_distinguished():
    """Nothing returned from a populated source, vs. an empty source itself."""
    populated_source = _evaluate([], rows_in=25)
    assert populated_source["extraction_passed"] is False
    assert "returned none" in populated_source["failure_reason"]

    empty_source = _evaluate([], rows_in=0)
    assert empty_source["extraction_passed"] is False
    assert "no data rows" in empty_source["failure_reason"]

    print("✓ test_no_rows_cases_are_distinguished PASSED")


def test_retry_behavior():
    """A script that would not run is retried with the error - until the ceiling."""
    first_attempt = _evaluate([], status="error",
                              error="NameError: name 'person' is not defined")
    assert first_attempt["extraction_passed"] is False
    assert first_attempt["should_retry"] is True
    assert "NameError" in first_attempt["failure_reason"]

    final_attempt = _evaluate(_rows(1, 49), rows_in=50,
                              attempt=EvaluateExtractionTool.MAX_ATTEMPTS)
    assert final_attempt["extraction_passed"] is False
    assert final_attempt["should_retry"] is False

    print("✓ test_retry_behavior PASSED")


def test_column_delivery_threshold():
    """Real case: a staff roster whose table is 26 columns wide. The script
    returned four values per row, and because the caller pairs position to
    name, those four were labelled with the header's first four names - Team,
    Skill, Aze_User, Name Surname - holding a user id and a duration. Nothing
    else catches it: the rows parse, and the row count matches. A column or
    two short of that is normal trimming, not a mislabelling.
    """
    short_rows = [
        {"c0": "v", "c1": "v", "c2": "v", "c3": "v", "_valid": True, "_row_number": i}
        for i in range(20)
    ]
    short = _evaluate(short_rows, rows_in=20,
                      report={"modal_field_count": 26, "header_field_count": 43})
    assert short["extraction_passed"] is False
    assert short["should_retry"] is True
    assert "wrong column names" in short["failure_reason"]
    assert "26 values per row" in short["failure_reason"]
    assert short["evaluation"]["column_delivery"] == round(4 / 26, 3)

    slightly_short_rows = [
        {f"c{i}": "v" for i in range(25)} | {"_valid": True, "_row_number": n}
        for n in range(20)
    ]
    slightly_short = _evaluate(slightly_short_rows, rows_in=20,
                               report={"modal_field_count": 26})
    assert slightly_short["extraction_passed"] is True, slightly_short["failure_reason"]

    print("✓ test_column_delivery_threshold PASSED")


def test_missing_execution_result_is_an_error():
    r = json.loads(EvaluateExtractionTool()({"guid": "g", "metadata_report": REPORT}))

    assert r["status"] == "error"
    assert "execution_result" in r["error"]

    print("✓ test_missing_execution_result_is_an_error PASSED")


def run_all_tests():
    tests = [
        test_passing_cases,
        test_quality_threshold_failures,
        test_no_rows_cases_are_distinguished,
        test_retry_behavior,
        test_column_delivery_threshold,
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
