"""Tests for fetch_and_sample tool (the Micro-Slicer half of the Looker)."""
import json
import tempfile
import os

from tools.fetch_and_sample.tool import FetchAndSampleTool


def test_fetch_csv_file():
    """Test fetching a CSV file."""
    csv_content = "id,name,email\n1,John,john@example.com\n2,Jane,jane@example.com\n3,Bob,bob@example.com\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write(csv_content)
        temp_path = f.name

    try:
        tool = FetchAndSampleTool()
        response = json.loads(tool({"source_path": temp_path, "sample_size": 10}))

        assert response["status"] == "success"
        assert response["source_type"] == "local_file"
        assert response["raw_sample"] is not None
        assert response["error"] is None
        assert response["detected_format_hint"] == "csv"
        assert response["first_line_is_header"] is True
        assert "John" in response["raw_sample"]
        assert "timestamp" in response and "T" in response["timestamp"]

        print("✓ test_fetch_csv_file PASSED")

    finally:
        os.unlink(temp_path)


def test_error_handling():
    """Missing source and a nonexistent path are both reported as errors."""
    tool = FetchAndSampleTool()

    missing = json.loads(tool({"sample_size": 10}))
    assert missing["status"] == "error"
    assert "source_path" in missing["error"]

    nonexistent = json.loads(tool({"source_path": "/nonexistent/path/to/file.csv"}))
    assert nonexistent["status"] == "error"
    assert nonexistent["error"] is not None

    print("✓ test_error_handling PASSED")


def test_tool_metadata():
    """Test tool metadata."""
    tool = FetchAndSampleTool()

    assert tool.name == "fetch_and_sample"
    assert "sample" in tool.description.lower()
    assert "source_path" in tool.input_schema["properties"]
    assert "body_text" in tool.input_schema["properties"]
    assert "status" in tool.output_schema["properties"]
    # Either source_path or body_text is required (via anyOf constraint)
    assert "anyOf" in tool.input_schema or len(tool.input_schema["required"]) == 0

    print("✓ test_tool_metadata PASSED")


def test_micro_slicer_takes_head_and_tail_not_the_middle():
    """The Looker needs the document's complete bounding box: a fixed head
    window and a fixed tail window, not a spread sample from the middle."""
    tool = FetchAndSampleTool()
    lines = [f"row_{i}" for i in range(200)]
    body_text = "\n".join(lines)

    response = json.loads(tool({"body_text": body_text, "guid": "g"}))

    assert response["status"] == "success", response
    sample_lines = response["raw_sample"].split("\n")
    head = tool.MICRO_SLICE_HEAD_LINES
    tail = tool.MICRO_SLICE_TAIL_LINES

    assert sample_lines[:head] == lines[:head], "head must be the document's opening lines"
    assert sample_lines[-tail:] == lines[-tail:], "tail must be the document's closing lines"
    assert len(sample_lines) == head + tail
    # a middle row must not appear - the whole point is head+tail, not a spread
    assert "row_100" not in response["raw_sample"]

    print("✓ test_micro_slicer_takes_head_and_tail_not_the_middle PASSED")


def test_small_document_is_returned_in_full():
    """A document smaller than the head+tail window needs no slicing at all."""
    tool = FetchAndSampleTool()
    response = json.loads(tool({"body_text": "a,b\n1,2\n3,4", "guid": "g"}))

    assert response["status"] == "success"
    assert response["sample_size"] == 3

    print("✓ test_small_document_is_returned_in_full PASSED")


def test_large_document_stays_bounded_by_the_head_tail_window():
    """sample_size no longer drives how much is returned - the fixed
    head+tail window does, however large the document is."""
    tool = FetchAndSampleTool()
    lines = ["id,name,data"] + [f"{i},name_{i},data_{i}" for i in range(100000)]
    body_text = "\n".join(lines)

    response = json.loads(tool({"body_text": body_text, "sample_size": 10}))

    assert response["status"] == "success"
    assert response["sample_size"] == tool.MICRO_SLICE_HEAD_LINES + tool.MICRO_SLICE_TAIL_LINES
    assert response["total_bytes"] == len(body_text.encode("utf-8"))
    # the slice itself stays small even though the document is not
    assert len(response["raw_sample"].encode("utf-8")) <= tool.MICRO_SLICE_MAX_BYTES

    print("✓ test_large_document_stays_bounded_by_the_head_tail_window PASSED")


def test_a_short_header_line_survives_a_long_outlier_line():
    """A real bug: a short header line and one enormous comment cell used to
    be trimmed to the identical per-line byte cap, truncating the header
    mid-word ("Language" -> "Lang") even though it was nowhere near using its
    share of the budget. Trimming must protect short lines and absorb the
    trim into the long outlier instead."""
    tool = FetchAndSampleTool()
    header = "Week,TL,Agent,Skill,Language,SPD,THT,TO%,NPS,CLS,TT"
    huge_comment_row = "1,a,b,c,English," + ("x" * 5000) + ",1,2,3,4,5"
    lines = [header, huge_comment_row] + [f"{i},a,b,c,English,1,2,3,4,5,6" for i in range(80)]
    body_text = "\n".join(lines)

    response = json.loads(tool({"body_text": body_text, "guid": "g"}))

    assert response["status"] == "success"
    sample_lines = response["raw_sample"].split("\n")
    assert sample_lines[0] == header, "the short header must survive whole, untouched"

    print("✓ test_a_short_header_line_survives_a_long_outlier_line PASSED")


def test_edge_cases_do_not_crash():
    """Empty files, header-only files, and degenerate sample_size/skip_rows
    values are all handled without raising."""
    tool = FetchAndSampleTool()

    # An empty document is nothing to sample, not a tool failure - body_text
    # must be truthy for the anyOf input contract, so this goes through
    # source_path instead, where an empty file is a legitimate input.
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        temp_path = f.name
    try:
        empty = json.loads(tool({"source_path": temp_path}))
        assert empty["status"] == "success"
        assert empty["raw_sample"] == ""
    finally:
        os.unlink(temp_path)

    header_only = json.loads(tool({"body_text": "id,name,email"}))
    assert header_only["status"] == "success"
    assert header_only["total_records"] == 1

    zero_sample = json.loads(tool({"body_text": "id,name\n1,John\n2,Jane", "sample_size": 0}))
    assert zero_sample["status"] == "success"

    skip_past_end = json.loads(tool({"body_text": "id,name\n1,John", "skip_rows": 100}))
    assert skip_past_end["status"] == "success"

    print("✓ test_edge_cases_do_not_crash PASSED")


def test_special_characters_and_multiline_quoted_fields_are_preserved():
    """Accented characters and a newline inside a quoted field must survive
    the fetch untouched - this tool reads raw text, not parsed CSV."""
    tool = FetchAndSampleTool()

    accented = json.loads(tool({"body_text": "id,name,comment\n1,José,Café\n2,François,Élève"}))
    assert accented["status"] == "success"
    assert "Café" in accented["raw_sample"]

    multiline = json.loads(tool({"body_text": 'id,comment\n1,"Line 1\nLine 2"'}))
    assert multiline["status"] == "success"

    print("✓ test_special_characters_and_multiline_quoted_fields_are_preserved PASSED")


def test_format_detection_across_delimiters_and_json():
    tool = FetchAndSampleTool()

    for body_text, expected in [
        ("id,name,value\n1,test,100", "csv"),
        ("id|name|value\n1|test|100", "pipe"),
        ("id\tname\tvalue\n1\ttest\t100", "tab"),
        ('{"id": 1, "name": "test"}', "json"),
    ]:
        response = json.loads(tool({"body_text": body_text}))
        assert response["status"] == "success"
        assert response["detected_format_hint"] == expected, (body_text, response)

    print("✓ test_format_detection_across_delimiters_and_json PASSED")


def run_all_tests():
    """Run all tests."""
    tests = [
        test_fetch_csv_file,
        test_error_handling,
        test_tool_metadata,
        test_micro_slicer_takes_head_and_tail_not_the_middle,
        test_small_document_is_returned_in_full,
        test_large_document_stays_bounded_by_the_head_tail_window,
        test_a_short_header_line_survives_a_long_outlier_line,
        test_edge_cases_do_not_crash,
        test_special_characters_and_multiline_quoted_fields_are_preserved,
        test_format_detection_across_delimiters_and_json,
    ]

    passed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"✗ {test.__name__} FAILED: {str(e)}")

    print(f"\n{'='*60}")
    print(f"Test Results: {passed}/{len(tests)} passed")
    print(f"{'='*60}")

    return passed == len(tests)


if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)
