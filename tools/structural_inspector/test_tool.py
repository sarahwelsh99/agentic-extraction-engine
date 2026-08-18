"""Tests for structural_inspector (Tool 2).

Hits the real vLLM server, following the same convention as
generate_parser_script/test_tool.py (CLAUDE.md: "most tool tests call the
real server rather than mocking it"). Requires:
    curl http://localhost:8000/v1/models
"""

import json
from tools.structural_inspector.tool import StructuralInspectorTool


def _inspect(sample, **kwargs):
    tool = StructuralInspectorTool()
    return json.loads(tool({"raw_sample": sample, **kwargs}))


def test_simple_csv_is_read_and_adapted_for_tool3_and_tool4():
    """A plain CSV yields a looker_spec and a metadata_report Tool 3/4 can use."""
    sample = (
        "employee_id,first_name,last_name,email\n"
        "10001,John,Smith,john@company.com\n"
        "10002,Jane,Doe,jane@company.com"
    )
    r = _inspect(sample)

    assert r["status"] == "success", r
    assert r["rejected"] is False
    spec = r["looker_spec"]
    assert spec["format_spec"]["delimiter_type"] == "comma"
    assert spec["head_bounds"]["has_header"] is True

    report = r["metadata_report"]
    assert report["delimiter"] == ","
    assert report["header_row_index"] == 0
    assert report["header_field_count"] == 4
    assert report["modal_field_count"] == 4

    print("✓ test_simple_csv_is_read_and_adapted_for_tool3_and_tool4 PASSED")


def test_footer_and_null_tokens_are_reported():
    """A footer and non-blank null tokens surface in both spec and report."""
    sample = (
        "id,name,balance\n"
        "1,Alice,100.00\n"
        "2,Bob,N/A\n"
        "3,Carol,-\n"
        "Total: 3 rows\n"
        "Confidential"
    )
    r = _inspect(sample)

    assert r["status"] == "success", r
    spec = r["looker_spec"]
    assert spec["tail_bounds"]["has_footer"] is True
    assert spec["format_spec"]["null_values"], "N/A and - should be reported as nulls"

    report = r["metadata_report"]
    assert report["footer_start_from_bottom"] >= 1
    assert report["null_values"]

    print("✓ test_footer_and_null_tokens_are_reported PASSED")


def test_prose_report_is_rejected_as_not_tabular():
    """Prose/printed reports are refused, matching the retired heuristic's behaviour."""
    sample = (
        "Springfield Insurance Brokers - Detailed Renewal Report\n"
        "2022-08-22 - 2024-11-27\n"
        "Print Date: 2024-11-27 8:23AM EST\n"
        "Tel: 416-359-9339\n"
    )
    r = _inspect(sample, guid="report-1")

    assert r["rejected"] is True
    assert r["rejection_code"] in ("NOT_TABULAR", "NO_DATA_ROWS")

    print("✓ test_prose_report_is_rejected_as_not_tabular PASSED")


def test_missing_sample_is_an_error():
    r = json.loads(StructuralInspectorTool()({"raw_sample": ""}))
    assert r["status"] == "error"

    print("✓ test_missing_sample_is_an_error PASSED")


def run_all_tests():
    tests = [
        test_simple_csv_is_read_and_adapted_for_tool3_and_tool4,
        test_footer_and_null_tokens_are_reported,
        test_prose_report_is_rejected_as_not_tabular,
        test_missing_sample_is_an_error,
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
