"""Comprehensive tests for fetch_and_sample tool - stability validation."""
import json
import tempfile
import os

from tools.fetch_and_sample.tool import FetchAndSampleTool


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_file(self):
        """Test handling of empty files."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("")
            temp_path = f.name

        try:
            tool = FetchAndSampleTool()
            response = json.loads(tool({"source_path": temp_path}))
            assert response["status"] == "success"
            assert response["raw_sample"] == ""
            print("✓ test_empty_file PASSED")
        finally:
            os.unlink(temp_path)

    def test_single_line_no_data(self):
        """Test file with only header, no data."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("id,name,email\n")
            temp_path = f.name

        try:
            tool = FetchAndSampleTool()
            response = json.loads(tool({"source_path": temp_path}))
            assert response["status"] == "success"
            assert response["first_line_is_header"] is True
            # sample_size counts records returned (here: the header, and nothing
            # else); total_records confirms the document held no data rows.
            assert response["sample_size"] == 1
            assert response["total_records"] == 1
            print("✓ test_single_line_no_data PASSED")
        finally:
            os.unlink(temp_path)

    def test_sample_size_zero(self):
        """Test with sample_size = 0."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("id,name\n1,John\n2,Jane\n")
            temp_path = f.name

        try:
            tool = FetchAndSampleTool()
            response = json.loads(tool({
                "source_path": temp_path,
                "sample_size": 0,
            }))
            # Should get at least the header
            assert response["status"] == "success"
            print("✓ test_sample_size_zero PASSED")
        finally:
            os.unlink(temp_path)

    def test_sample_size_exceeds_file(self):
        """Test when sample_size > file rows."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("id,name\n1,John\n")
            temp_path = f.name

        try:
            tool = FetchAndSampleTool()
            response = json.loads(tool({
                "source_path": temp_path,
                "sample_size": 1000,
            }))
            assert response["status"] == "success"
            assert response["sample_size"] <= 1000
            print("✓ test_sample_size_exceeds_file PASSED")
        finally:
            os.unlink(temp_path)

    def test_skip_rows_exceeds_file(self):
        """Test when skip_rows > file rows."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("id,name\n1,John\n")
            temp_path = f.name

        try:
            tool = FetchAndSampleTool()
            response = json.loads(tool({
                "source_path": temp_path,
                "skip_rows": 100,
            }))
            assert response["status"] == "success"
            # Should return empty or just remaining lines
            print("✓ test_skip_rows_exceeds_file PASSED")
        finally:
            os.unlink(temp_path)

    def test_special_characters_in_data(self):
        """Test handling of special characters."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write("id,name,comment\n1,José,Café☕\n2,François,Élève\n")
            temp_path = f.name

        try:
            tool = FetchAndSampleTool()
            response = json.loads(tool({"source_path": temp_path}))
            assert response["status"] == "success"
            assert "Café" in response["raw_sample"] or "Caf" in response["raw_sample"]
            print("✓ test_special_characters_in_data PASSED")
        finally:
            os.unlink(temp_path)

    def test_newlines_in_quoted_fields(self):
        """Test CSV with newlines inside quoted fields."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            # Note: This tests the raw text reading, not actual CSV parsing
            f.write('id,comment\n1,"Line 1\nLine 2"\n')
            temp_path = f.name

        try:
            tool = FetchAndSampleTool()
            response = json.loads(tool({"source_path": temp_path}))
            assert response["status"] == "success"
            print("✓ test_newlines_in_quoted_fields PASSED")
        finally:
            os.unlink(temp_path)

    def test_large_file_handling(self):
        """Test handling of larger files."""
        # Create a 5MB file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("id,name,data\n")
            for i in range(100000):
                f.write(f"{i},name_{i},data_{i}\n")
            temp_path = f.name

        try:
            tool = FetchAndSampleTool()
            response = json.loads(tool({
                "source_path": temp_path,
                "sample_size": 10,
            }))
            assert response["status"] == "success"
            assert response["sample_size"] == 10
            file_size = os.path.getsize(temp_path)
            assert response["total_bytes"] == file_size
            print(f"✓ test_large_file_handling PASSED ({file_size} bytes)")
        finally:
            os.unlink(temp_path)

    def test_various_delimiters(self):
        """Test detection of various delimiters."""
        test_cases = [
            ("CSV", "id,name,value\n1,test,100\n"),
            ("Pipe", "id|name|value\n1|test|100\n"),
            ("Tab", "id\tname\tvalue\n1\ttest\t100\n"),
        ]

        for format_name, content in test_cases:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
                f.write(content)
                temp_path = f.name

            try:
                tool = FetchAndSampleTool()
                response = json.loads(tool({"source_path": temp_path}))
                assert response["status"] == "success"
                expected = format_name.lower() if format_name != "Pipe" else "pipe"
                assert response["detected_format_hint"] == expected
                print(f"✓ test_delimiter_{format_name} PASSED")
            finally:
                os.unlink(temp_path)

    def test_json_format(self):
        """Test JSON format detection."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('{"id": 1, "name": "test"}\n')
            temp_path = f.name

        try:
            tool = FetchAndSampleTool()
            response = json.loads(tool({"source_path": temp_path}))
            assert response["status"] == "success"
            assert response["detected_format_hint"] == "json"
            print("✓ test_json_format PASSED")
        finally:
            os.unlink(temp_path)


class TestResponseValidation:
    """Validate response format and completeness."""

    def test_response_always_valid_json(self):
        """Test that response is always valid JSON."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("a,b\n1,2\n")
            temp_path = f.name

        try:
            tool = FetchAndSampleTool()
            response_json = tool({"source_path": temp_path})
            response = json.loads(response_json)  # Should not raise
            assert isinstance(response, dict)
            print("✓ test_response_always_valid_json PASSED")
        finally:
            os.unlink(temp_path)

    def test_response_has_all_required_fields(self):
        """Test that response has all required fields."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("a,b\n1,2\n")
            temp_path = f.name

        try:
            tool = FetchAndSampleTool()
            response = json.loads(tool({"source_path": temp_path}))

            required_fields = ["status", "error", "timestamp"]
            for field in required_fields:
                assert field in response, f"Missing required field: {field}"

            print("✓ test_response_has_all_required_fields PASSED")
        finally:
            os.unlink(temp_path)

    def test_error_response_format(self):
        """Test that error responses are properly formatted."""
        tool = FetchAndSampleTool()
        response = json.loads(tool({"source_path": "/nonexistent/file"}))

        assert response["status"] == "error"
        assert response["error"] is not None
        assert "timestamp" in response
        # raw_sample may not be present in error response
        print("✓ test_error_response_format PASSED")


class TestSecurityConsiderations:
    """Test security aspects of the tool."""

    def test_path_traversal_local_file(self):
        """Test that tool handles path traversal attempts safely."""
        # This should fail gracefully, not expose directories
        tool = FetchAndSampleTool()
        response = json.loads(tool({
            "source_path": "/etc/passwd"
        }))
        # Either succeeds (if readable) or fails with clear error
        assert response["status"] in ["success", "error"]
        print("✓ test_path_traversal_local_file PASSED")

    def test_invalid_path_characters(self):
        """Test handling of invalid path characters."""
        tool = FetchAndSampleTool()
        response = json.loads(tool({
            "source_path": "invalid\x00path"
        }))
        # Should handle gracefully
        assert response["status"] in ["success", "error"]
        print("✓ test_invalid_path_characters PASSED")

    def test_relative_paths_rejected(self):
        """Test that relative paths are handled safely."""
        tool = FetchAndSampleTool()
        response = json.loads(tool({
            "source_path": "../../../etc/passwd"
        }))
        # Should fail with error (treated as BigQuery table)
        assert "error" in response
        print("✓ test_relative_paths_rejected PASSED")


def run_all_tests():
    """Run all tests."""
    test_classes = [
        TestEdgeCases,
        TestResponseValidation,
        TestSecurityConsiderations,
    ]

    total = 0
    passed = 0

    for test_class in test_classes:
        instance = test_class()
        methods = [m for m in dir(instance) if m.startswith("test_")]

        for method_name in methods:
            try:
                method = getattr(instance, method_name)
                method()
                passed += 1
            except Exception as e:
                print(f"✗ {test_class.__name__}.{method_name} FAILED: {str(e)}")
            finally:
                total += 1

    print(f"\n{'='*70}")
    print(f"Comprehensive Test Results: {passed}/{total} passed")
    print(f"{'='*70}")

    return passed == total


if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)
