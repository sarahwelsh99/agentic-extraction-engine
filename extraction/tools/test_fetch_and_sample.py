"""Tests for fetch_and_sample tool."""
import json
import tempfile
import os
from pathlib import Path

from extraction.tools.fetch_and_sample import (
    fetch_and_sample,
    _detect_header,
    _detect_format,
)


class TestFetchAndSampleLocalFile:
    """Test fetch_and_sample with local files."""

    def test_fetch_csv_file(self):
        """Test fetching a CSV file."""
        # Create temporary CSV file
        csv_content = "id,name,email\n1,John,john@example.com\n2,Jane,jane@example.com\n3,Bob,bob@example.com\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            temp_path = f.name

        try:
            input_data = {
                "source_path": temp_path,
                "sample_size": 10,
                "max_bytes": 1048576,
                "skip_rows": 0,
                "encoding": "utf-8",
            }

            response_json = fetch_and_sample(input_data)
            response = json.loads(response_json)

            # Validate response structure
            assert response["status"] == "success"
            assert response["source_type"] == "local_file"
            assert response["raw_sample"] is not None
            assert response["error"] is None
            assert response["encoding"] == "utf-8"
            assert response["detected_format_hint"] == "csv"
            assert response["first_line_is_header"] is True

            # Validate sample content
            assert "id,name,email" in response["raw_sample"]
            assert "John" in response["raw_sample"]
            assert response["sample_size"] == 3  # 3 data rows

            print("✓ test_fetch_csv_file PASSED")

        finally:
            os.unlink(temp_path)

    def test_fetch_with_skip_rows(self):
        """Test fetching with skip_rows parameter."""
        csv_content = "header1,header2\nrow1,val1\nrow2,val2\nrow3,val3\nrow4,val4\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            temp_path = f.name

        try:
            input_data = {
                "source_path": temp_path,
                "sample_size": 2,
                "skip_rows": 1,
                "encoding": "utf-8",
            }

            response = json.loads(fetch_and_sample(input_data))

            assert response["status"] == "success"
            assert "row2" in response["raw_sample"]
            # skip_rows=1 means we skip the header and get data rows
            print("✓ test_fetch_with_skip_rows PASSED")

        finally:
            os.unlink(temp_path)

    def test_fetch_nonexistent_file(self):
        """Test error handling for nonexistent file."""
        input_data = {
            "source_path": "/nonexistent/path/to/file.csv",
            "sample_size": 10,
        }

        response = json.loads(fetch_and_sample(input_data))

        assert response["status"] == "error"
        assert response["error"] is not None
        assert "No such file" in response["error"] or "not found" in response["error"]
        print("✓ test_fetch_nonexistent_file PASSED")

    def test_fetch_missing_source_path(self):
        """Test error handling for missing source_path."""
        input_data = {
            "sample_size": 10,
        }

        response = json.loads(fetch_and_sample(input_data))

        assert response["status"] == "error"
        assert "source_path is required" in response["error"]
        print("✓ test_fetch_missing_source_path PASSED")

    def test_sample_size_capped(self):
        """Test that sample_size is capped at 100."""
        csv_content = "\n".join([f"id{i},name{i}" for i in range(200)])
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            temp_path = f.name

        try:
            input_data = {
                "source_path": temp_path,
                "sample_size": 500,  # Request more than max
            }

            response = json.loads(fetch_and_sample(input_data))
            assert response["status"] == "success"
            # Sample size should be capped at 100
            assert response["sample_size"] <= 100
            print("✓ test_sample_size_capped PASSED")

        finally:
            os.unlink(temp_path)


class TestDetectHeader:
    """Test header detection heuristics."""

    def test_detect_common_header_keywords(self):
        """Test detection of common header keywords."""
        assert _detect_header("id,name,email") is True
        assert _detect_header("title,date,value") is True
        assert _detect_header("ID,NAME,EMAIL") is True
        print("✓ test_detect_common_header_keywords PASSED")

    def test_detect_numeric_first_row(self):
        """Test that numeric rows are not detected as headers."""
        assert _detect_header("1,2,3") is False
        assert _detect_header("100,200,300") is False
        print("✓ test_detect_numeric_first_row PASSED")

    def test_detect_empty_line(self):
        """Test empty line handling."""
        assert _detect_header("") is False
        print("✓ test_detect_empty_line PASSED")


class TestDetectFormat:
    """Test file format detection."""

    def test_detect_csv(self):
        """Test CSV detection."""
        assert _detect_format("id,name,email") == "csv"
        print("✓ test_detect_csv PASSED")

    def test_detect_pipe_delimited(self):
        """Test pipe-delimited detection."""
        assert _detect_format("id|name|email") == "pipe"
        print("✓ test_detect_pipe_delimited PASSED")

    def test_detect_json(self):
        """Test JSON detection."""
        assert _detect_format('{"id": 1, "name": "John"}') == "json"
        assert _detect_format("[1, 2, 3]") == "json"
        print("✓ test_detect_json PASSED")

    def test_detect_tab_delimited(self):
        """Test tab-delimited detection."""
        assert _detect_format("id\tname\temail") == "tab"
        print("✓ test_detect_tab_delimited PASSED")


class TestResponseStructure:
    """Test response JSON structure."""

    def test_response_always_has_timestamp(self):
        """Test that all responses have a timestamp."""
        csv_content = "id,name\n1,John\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            temp_path = f.name

        try:
            input_data = {"source_path": temp_path}
            response = json.loads(fetch_and_sample(input_data))

            assert "timestamp" in response
            assert "T" in response["timestamp"]  # ISO 8601 format
            print("✓ test_response_always_has_timestamp PASSED")

        finally:
            os.unlink(temp_path)

    def test_response_status_field(self):
        """Test that status field is always present."""
        # Success case
        csv_content = "a,b\n1,2\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            temp_path = f.name

        try:
            input_data = {"source_path": temp_path}
            response = json.loads(fetch_and_sample(input_data))
            assert response["status"] in ["success", "error", "partial_success"]
            print("✓ test_response_status_field PASSED")

        finally:
            os.unlink(temp_path)


def run_all_tests():
    """Run all tests."""
    test_classes = [
        TestFetchAndSampleLocalFile,
        TestDetectHeader,
        TestDetectFormat,
        TestResponseStructure,
    ]

    total_tests = 0
    passed_tests = 0

    for test_class in test_classes:
        instance = test_class()
        methods = [m for m in dir(instance) if m.startswith("test_")]

        for method_name in methods:
            try:
                method = getattr(instance, method_name)
                method()
                passed_tests += 1
            except Exception as e:
                print(f"✗ {test_class.__name__}.{method_name} FAILED: {str(e)}")
            finally:
                total_tests += 1

    print(f"\n{'='*60}")
    print(f"Test Results: {passed_tests}/{total_tests} passed")
    print(f"{'='*60}")

    return passed_tests == total_tests


if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)
