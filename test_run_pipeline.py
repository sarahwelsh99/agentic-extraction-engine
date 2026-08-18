"""Tests for run_pipeline.py's merge logic.

run_document() (extraction/core/pipeline_agent.py) is faked here with a
canned list of PipelineStates, so these pin the merge - row tagging with
_sheet_name, results["sheets"], and "at least one sheet passed" success -
not the agent loop or any real tool. record_pipeline_run is also faked: the
real one appends to the git-tracked metrics.csv, which a test run must not
touch.
"""

import run_pipeline as rp
from extraction.core.pipeline_agent import PipelineState


def _state(sheet_name=None, status="success", rows=None, **kw):
    s = PipelineState(guid="g", sheet_name=sheet_name, status=status, **kw)
    s.extracted_rows = rows or []
    return s


def _run_with(states, load=False):
    """Run run_pipeline() with run_document() and metrics recording faked,
    restoring both afterward regardless of outcome."""
    original_run_document = rp.run_document
    original_record = rp.record_pipeline_run

    async def fake_run_document(guid, body_text, **kw):
        return states

    rp.run_document = fake_run_document
    rp.record_pipeline_run = lambda **kw: {"csv_path": "", "json_path": ""}
    try:
        return rp.run_pipeline("g", body_text="irrelevant", load=load)
    finally:
        rp.run_document = original_run_document
        rp.record_pipeline_run = original_record


def test_single_sheet_success_matches_the_original_shape():
    """An ordinary document (one sheet, name=None) produces output
    indistinguishable from before sheet detection existed - no _sheet_name key."""
    result = _run_with([_state(rows=[{"id": "1"}])])

    assert result["success"] is True
    assert result["extraction_passed"] is True
    assert result["extracted_rows"] == [{"id": "1"}]
    assert len(result["sheets"]) == 1
    assert result["sheets"][0] == {
        "sheet_name": None, "status": "success",
        "stage_failed": None, "failure_reason": None, "rows_extracted": 1,
    }

    print("✓ test_single_sheet_success_matches_the_original_shape PASSED")


def test_multi_sheet_partial_success_tags_rows_and_reports_each_sheet():
    """Confirmed product behavior: partial success counts, and the specific
    failing sheet/stage/reason is captured, not just a rolled-up count."""
    result = _run_with([
        _state(sheet_name="A", status="success", rows=[{"id": "1"}]),
        _state(sheet_name="B", status="failed", failure_reason="bad rows",
                stage_log=[{"stage": "eval", "attempt": 1, "start": 0, "end": 0,
                           "status": "error", "response": {}}]),
    ])

    assert result["success"] is True
    assert result["extraction_passed"] is True
    assert result["extracted_rows"] == [{"id": "1", "_sheet_name": "A"}]
    assert len(result["sheets"]) == 2

    by_name = {s["sheet_name"]: s for s in result["sheets"]}
    assert by_name["A"]["status"] == "success"
    assert by_name["B"]["status"] == "failed"
    assert by_name["B"]["stage_failed"] == "eval"
    assert by_name["B"]["failure_reason"] == "bad rows"

    print("✓ test_multi_sheet_partial_success_tags_rows_and_reports_each_sheet PASSED")


def test_all_sheets_rejected_is_reported_as_rejected():
    result = _run_with([
        _state(sheet_name="A", status="rejected",
              rejection_code="NOT_TABULAR", rejection_reason="prose, not a table"),
    ])

    assert result["success"] is False
    assert result["rejected"] is True
    assert result["rejection_code"] == "NOT_TABULAR"
    assert result["rejection_reason"] == "prose, not a table"

    print("✓ test_all_sheets_rejected_is_reported_as_rejected PASSED")


def test_all_sheets_failed_surfaces_a_specific_reason():
    """When nothing passed and it wasn't a rejection, the richest single
    failure surfaces at the top level for backward compatibility."""
    result = _run_with([
        _state(sheet_name="A", status="failed", failure_reason="only 10% valid rows"),
    ])

    assert result["success"] is False
    assert result["extraction_passed"] is False
    assert result["failure_reason"] == "only 10% valid rows"

    print("✓ test_all_sheets_failed_surfaces_a_specific_reason PASSED")


def run_all_tests():
    tests = [
        test_single_sheet_success_matches_the_original_shape,
        test_multi_sheet_partial_success_tags_rows_and_reports_each_sheet,
        test_all_sheets_rejected_is_reported_as_rejected,
        test_all_sheets_failed_surfaces_a_specific_reason,
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
