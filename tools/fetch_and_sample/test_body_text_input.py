"""Tests for body_text input (e.g., from glean.drive_files)."""
import json

from tools.fetch_and_sample.tool import FetchAndSampleTool


class TestBodyTextInput:
    """Test fetching from body_text directly."""

    def test_body_text_csv(self):
        """Test with CSV data in body_text."""
        body_text = "id,name,email\n1,Alice,alice@test.com\n2,Bob,bob@test.com\n3,Carol,carol@test.com\n"

        tool = FetchAndSampleTool()
        response = json.loads(tool({
            "body_text": body_text,
            "sample_size": 2,
        }))

        assert response["status"] == "success"
        assert response["source_type"] == "glean_document"
        assert response["detected_format_hint"] == "csv"
        assert response["first_line_is_header"] is True
        assert response["actual_header_row_index"] == 0
        assert "1,Alice,alice@test.com" in response["raw_sample"]
        print("✓ test_body_text_csv PASSED")

    def test_body_text_with_guid(self):
        """Test body_text with guid metadata."""
        body_text = "id,name,email\n1,Alice,alice@test.com\n"
        guid = "ddffbdb6-5041-4d65-a744-5a0631a629aa"

        tool = FetchAndSampleTool()
        response = json.loads(tool({
            "guid": guid,
            "body_text": body_text,
            "sample_size": 5,
        }))

        assert response["status"] == "success"
        assert response["guid"] == guid
        assert response["source_type"] == "glean_document"
        print("✓ test_body_text_with_guid PASSED")

    def test_body_text_with_metadata_comments(self):
        """Test body_text with metadata before actual headers."""
        body_text = """Location,Employee ID,Legal First Name,Legal Last Name
ZA - Cape Town, 10259248,Aphindiwe,Mdolomba
ZA - Cape Town, 10259275,Darren,Arnold
ZA - Cape Town, 10259240,Adam,Fortuin
"""

        tool = FetchAndSampleTool()
        response = json.loads(tool({
            "body_text": body_text,
            "sample_size": 2,
        }))

        assert response["status"] == "success"
        assert response["source_type"] == "glean_document"
        assert response["detected_format_hint"] == "csv"
        assert "10259248" in response["raw_sample"]
        print("✓ test_body_text_with_metadata_comments PASSED")

    def test_body_text_with_skip_rows(self):
        """Test skipping rows in body_text."""
        body_text = "# Comment line 1\n# Comment line 2\nid,name\n1,Alice\n2,Bob\n"

        tool = FetchAndSampleTool()
        response = json.loads(tool({
            "body_text": body_text,
            "skip_rows": 2,
            "sample_size": 2,
        }))

        assert response["status"] == "success"
        # Skip first 2 lines (comments), then get header + 2 data rows
        assert "id,name" in response["raw_sample"]
        assert "1,Alice" in response["raw_sample"]
        print("✓ test_body_text_with_skip_rows PASSED")

    def test_body_text_with_heuristic_header(self):
        """Test finding headers in body_text using heuristic."""
        body_text = """Some metadata line
Another metadata line
id,name,email,salary
1,Alice,alice@test.com,50000
2,Bob,bob@test.com,60000
"""

        tool = FetchAndSampleTool()
        response = json.loads(tool({
            "body_text": body_text,
            "find_header_heuristic": True,
            "sample_size": 2,
        }))

        assert response["status"] == "success"
        # Should find row 2 with keywords (id, name, email, salary)
        assert response["actual_header_row_index"] == 2
        assert response["first_line_is_header"] is True
        print("✓ test_body_text_with_heuristic_header PASSED")

    def test_body_text_requires_input(self):
        """Test that either body_text, source_path, or fetch_from_glean is required."""
        tool = FetchAndSampleTool()
        response = json.loads(tool({}))

        assert response["status"] == "error"
        assert any(phrase in response["error"] for phrase in [
            "fetch_from_glean",
            "source_path",
            "body_text"
        ])
        print("✓ test_body_text_requires_input PASSED")

    def test_body_text_json_format(self):
        """Test JSON data in body_text."""
        body_text = '{"id": 1, "name": "Alice"}\n{"id": 2, "name": "Bob"}\n'

        tool = FetchAndSampleTool()
        response = json.loads(tool({
            "body_text": body_text,
            "sample_size": 2,
        }))

        assert response["status"] == "success"
        assert response["detected_format_hint"] == "json"
        print("✓ test_body_text_json_format PASSED")

    def test_body_text_pipe_delimited(self):
        """Test pipe-delimited data in body_text."""
        body_text = "id|name|email\n1|Alice|alice@test.com\n2|Bob|bob@test.com\n"

        tool = FetchAndSampleTool()
        response = json.loads(tool({
            "body_text": body_text,
            "sample_size": 2,
        }))

        assert response["status"] == "success"
        assert response["detected_format_hint"] == "pipe"
        print("✓ test_body_text_pipe_delimited PASSED")

    def test_body_text_large_content(self):
        """max_bytes bounds the sample; the document is still read in full.

        Truncating the document before sampling would confine the sample to its
        opening rows, so total_bytes reports the real size and only raw_sample
        is capped.
        """
        # Create large body_text
        large_body = "id,name,email\n"
        for i in range(1000):
            large_body += f"{i},name_{i},email_{i}@test.com\n"

        tool = FetchAndSampleTool()
        response = json.loads(tool({
            "body_text": large_body,
            "max_bytes": 500,  # Only 500 bytes
            "sample_size": 10,
        }))

        assert response["status"] == "success"
        # total_bytes is the true document size, not the truncated size
        assert response["total_bytes"] == len(large_body.encode("utf-8"))
        # raw_sample is capped by max_bytes
        assert len(response["raw_sample"].encode("utf-8")) <= 500
        # ...and sampling still reached the end of the document
        assert response["sampled_record_indices"][-1] == response["total_records"] - 1
        print("✓ test_body_text_large_content PASSED")


def run_all_tests():
    """Run all tests."""
    instance = TestBodyTextInput()
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
    print(f"Body Text Input Tests: {passed}/{total} passed")
    print(f"{'='*70}")

    return passed == total


if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)
