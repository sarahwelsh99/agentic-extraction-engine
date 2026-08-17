"""Tests for header row detection at different positions."""
import json
import tempfile
import os

from tools.fetch_and_sample.tool import FetchAndSampleTool


class TestHeaderDetection:
    """Test finding headers at different row positions."""

    def test_header_at_row_0_default(self):
        """Test with headers at row 0 (default behavior)."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("id,name,email\n1,Alice,alice@test.com\n2,Bob,bob@test.com\n")
            temp_path = f.name

        try:
            tool = FetchAndSampleTool()
            response = json.loads(tool({"source_path": temp_path}))

            assert response["status"] == "success"
            assert response["actual_header_row_index"] == 0
            assert response["first_line_is_header"] is True
            print("✓ test_header_at_row_0_default PASSED")
        finally:
            os.unlink(temp_path)

    def test_header_at_row_3_explicit(self):
        """Test with headers at row 3 (explicitly specified)."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("# Comment line 1\n")
            f.write("# Comment line 2\n")
            f.write("# Metadata: version=1\n")
            f.write("id,name,email\n1,Alice,alice@test.com\n2,Bob,bob@test.com\n")
            temp_path = f.name

        try:
            tool = FetchAndSampleTool()
            response = json.loads(tool({
                "source_path": temp_path,
                "header_row_index": 3,
            }))

            assert response["status"] == "success"
            assert response["actual_header_row_index"] == 3
            assert response["first_line_is_header"] is True
            print("✓ test_header_at_row_3_explicit PASSED")
        finally:
            os.unlink(temp_path)

    def test_find_header_heuristic(self):
        """Test finding headers using heuristic search."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("# This is a comment\n")
            f.write("# Another comment\n")
            f.write("some,random,data\n")
            f.write("id,name,email\n")  # This should be detected as header
            f.write("1,Alice,alice@test.com\n")
            f.write("2,Bob,bob@test.com\n")
            temp_path = f.name

        try:
            tool = FetchAndSampleTool()
            response = json.loads(tool({
                "source_path": temp_path,
                "find_header_heuristic": True,
            }))

            assert response["status"] == "success"
            # Should find row 3 with id,name,email
            assert response["actual_header_row_index"] == 3
            assert response["first_line_is_header"] is True
            print("✓ test_find_header_heuristic PASSED")
        finally:
            os.unlink(temp_path)

    def test_header_with_keywords(self):
        """Test header detection with common keywords."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("garbage_row_1\n")
            f.write("garbage_row_2\n")
            f.write("user_id,user_name,email_address\n")  # Keywords: id, name, email
            f.write("001,Alice,alice@test.com\n")
            f.write("002,Bob,bob@test.com\n")
            temp_path = f.name

        try:
            tool = FetchAndSampleTool()
            response = json.loads(tool({
                "source_path": temp_path,
                "find_header_heuristic": True,
            }))

            assert response["status"] == "success"
            assert response["actual_header_row_index"] == 2
            assert response["first_line_is_header"] is True
            print("✓ test_header_with_keywords PASSED")
        finally:
            os.unlink(temp_path)

    def test_header_with_numeric_data_above(self):
        """Test that numeric rows don't get confused as headers."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("100,200,300\n")  # Numeric data, shouldn't be detected as header
            f.write("50,60,70\n")
            f.write("id,value,count\n")  # This is the real header
            f.write("1,100,5\n")
            f.write("2,200,10\n")
            temp_path = f.name

        try:
            tool = FetchAndSampleTool()
            response = json.loads(tool({
                "source_path": temp_path,
                "find_header_heuristic": True,
            }))

            assert response["status"] == "success"
            # Should skip numeric rows and find the one with keywords
            assert response["actual_header_row_index"] == 2
            assert response["first_line_is_header"] is True
            print("✓ test_header_with_numeric_data_above PASSED")
        finally:
            os.unlink(temp_path)

    def test_empty_file_with_header_search(self):
        """Test header search on empty file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("")
            temp_path = f.name

        try:
            tool = FetchAndSampleTool()
            response = json.loads(tool({
                "source_path": temp_path,
                "find_header_heuristic": True,
            }))

            assert response["status"] == "success"
            assert response["actual_header_row_index"] == 0
            print("✓ test_empty_file_with_header_search PASSED")
        finally:
            os.unlink(temp_path)


def run_all_tests():
    """Run all tests."""
    instance = TestHeaderDetection()
    methods = [m for m in dir(instance) if m.startswith("test_")]

    total = 0
    passed = 0

    for method_name in methods:
        try:
            method = getattr(instance, method_name)
            method()
            passed += 1
        except Exception as e:
            print(f"✗ {method_name} FAILED: {str(e)}")
        finally:
            total += 1

    print(f"\n{'='*70}")
    print(f"Header Detection Tests: {passed}/{total} passed")
    print(f"{'='*70}")

    return passed == total


if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)
