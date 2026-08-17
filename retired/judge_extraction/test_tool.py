"""Tests for judge_extraction (Tool 5).

Tool 5 scores an execution and routes it: write, reprocess or abandon. These
tests pin the routing, because that is what decides whether a document reaches
GCS, goes round again, or is dropped.
"""

import json
from tools.judge_extraction.tool import JudgeExtractionTool

REPORT = {"delimiter": ",", "header_names": ["id", "name", "email"]}


def _judge(rows, attempt=1, total_rows=None, status="success", error=None):
    tool = JudgeExtractionTool()
    execution = {
        "status": status,
        "extracted_rows": rows,
        "total_rows": total_rows if total_rows is not None else len(rows),
    }
    if error:
        execution["error"] = error
    return json.loads(tool({
        "guid": "test-guid",
        "execution_result": execution,
        "metadata_report": REPORT,
        "attempt": attempt,
    }))


def _rows(valid, invalid):
    rows = [
        {"id": i, "name": "n", "email": "e@x.com", "_valid": True, "_row_number": i}
        for i in range(valid)
    ]
    rows += [
        {"id": None, "_valid": False, "_errors": ["bad value"], "_row_number": 100 + i}
        for i in range(invalid)
    ]
    return rows


def test_clean_extraction_is_written():
    """A clean extraction proceeds to the writer."""
    r = _judge(_rows(20, 0))

    assert r["verdict"] == "success"
    assert r["decision"] == JudgeExtractionTool.WRITE
    assert r["feedback"] is None
    assert r["quality_metrics"]["success_rate"] == 1.0

    print("✓ test_clean_extraction_is_written PASSED")


def test_failure_on_first_attempt_is_reprocessed():
    """A bad extraction goes round again, carrying what went wrong."""
    r = _judge(_rows(2, 18), attempt=1)

    assert r["verdict"] == "failure"
    assert r["decision"] == JudgeExtractionTool.REPROCESS
    # The feedback must be specific enough to change the next attempt
    assert "bad value" in r["feedback"], r["feedback"]
    assert "20 rows" in r["feedback"] or "2 were valid" in r["feedback"], r["feedback"]

    print("✓ test_failure_on_first_attempt_is_reprocessed PASSED")


def test_failure_on_last_attempt_is_abandoned():
    """Retrying forever is not an option; the last attempt abandons."""
    r = _judge(_rows(2, 18), attempt=JudgeExtractionTool.MAX_ATTEMPTS)

    assert r["verdict"] == "failure"
    assert r["decision"] == JudgeExtractionTool.ABANDON

    print("✓ test_failure_on_last_attempt_is_abandoned PASSED")


def test_execution_failure_is_reprocessed_with_the_error():
    """A script that would not run is exactly what a retry should fix."""
    r = _judge([], status="error", error="NameError: name 'person' is not defined")

    assert r["verdict"] == "failure"
    assert r["decision"] == JudgeExtractionTool.REPROCESS
    assert "NameError" in r["feedback"], r["feedback"]

    print("✓ test_execution_failure_is_reprocessed_with_the_error PASSED")


def test_empty_document_is_abandoned_not_retried():
    """Nothing came out and nothing broke: a retry would read the same rows.

    This must not be scored as a failure either — success_rate is 0.0 both when
    the parser broke and when the rows were genuinely empty.
    """
    r = _judge([], total_rows=25)

    assert r["verdict"] == "no_records"
    assert r["decision"] == JudgeExtractionTool.ABANDON
    assert "25 rows" in r["reasoning"]

    print("✓ test_empty_document_is_abandoned_not_retried PASSED")


def test_no_rows_to_parse_is_its_own_outcome():
    """No input rows at all is distinct from rows that yielded nothing."""
    r = _judge([], total_rows=0)

    assert r["verdict"] == "no_data"
    assert r["decision"] == JudgeExtractionTool.ABANDON

    print("✓ test_no_rows_to_parse_is_its_own_outcome PASSED")


def test_always_empty_columns_are_reported_in_feedback():
    """Columns empty in every row suggest fields were read from wrong positions."""
    rows = [
        {"id": 1, "name": None, "email": None, "_valid": False,
         "_errors": ["missing"], "_row_number": 2}
        for _ in range(10)
    ]
    r = _judge(rows)

    assert r["decision"] == JudgeExtractionTool.REPROCESS
    assert "wrong positions" in r["feedback"], r["feedback"]
    assert r["quality_metrics"]["always_empty_column_count"] == 2

    print("✓ test_always_empty_columns_are_reported_in_feedback PASSED")


def test_missing_execution_result_is_an_error():
    """Nothing to judge is a tool error, not a verdict."""
    tool = JudgeExtractionTool()
    r = json.loads(tool({"guid": "g", "metadata_report": REPORT}))

    assert r["status"] == "error"
    assert "execution_result" in r["error"]

    print("✓ test_missing_execution_result_is_an_error PASSED")


def run_all_tests():
    tests = [
        test_clean_extraction_is_written,
        test_failure_on_first_attempt_is_reprocessed,
        test_failure_on_last_attempt_is_abandoned,
        test_execution_failure_is_reprocessed_with_the_error,
        test_empty_document_is_abandoned_not_retried,
        test_no_rows_to_parse_is_its_own_outcome,
        test_always_empty_columns_are_reported_in_feedback,
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
