"""Tests for fetch_and_sample tool."""
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
        input_data = {
            "source_path": temp_path,
            "sample_size": 10,
        }

        response_json = tool(input_data)
        response = json.loads(response_json)

        assert response["status"] == "success"
        assert response["source_type"] == "local_file"
        assert response["raw_sample"] is not None
        assert response["error"] is None
        assert response["detected_format_hint"] == "csv"
        assert response["first_line_is_header"] is True
        assert "John" in response["raw_sample"]

        print("✓ test_fetch_csv_file PASSED")

    finally:
        os.unlink(temp_path)


def test_missing_source_path():
    """Test error handling for missing source_path."""
    tool = FetchAndSampleTool()
    input_data = {"sample_size": 10}

    response = json.loads(tool(input_data))

    assert response["status"] == "error"
    assert "source_path" in response["error"]
    print("✓ test_missing_source_path PASSED")


def test_nonexistent_file():
    """Test error handling for nonexistent file."""
    tool = FetchAndSampleTool()
    input_data = {"source_path": "/nonexistent/path/to/file.csv"}

    response = json.loads(tool(input_data))

    assert response["status"] == "error"
    assert response["error"] is not None
    print("✓ test_nonexistent_file PASSED")


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


def test_response_has_timestamp():
    """Test that response always has timestamp."""
    csv_content = "a,b\n1,2\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write(csv_content)
        temp_path = f.name

    try:
        tool = FetchAndSampleTool()
        response = json.loads(tool({"source_path": temp_path}))

        assert "timestamp" in response
        assert "T" in response["timestamp"]
        print("✓ test_response_has_timestamp PASSED")

    finally:
        os.unlink(temp_path)


def run_all_tests():
    """Run all tests."""
    tests = [
        test_fetch_csv_file,
        test_missing_source_path,
        test_nonexistent_file,
        test_tool_metadata,
        test_response_has_timestamp,
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
