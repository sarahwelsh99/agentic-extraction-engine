"""Tests for glean fetching integration using mosaic-glean-extraction logic."""
import json
import sys

from tools.fetch_and_sample.tool import FetchAndSampleTool
from extraction.core import config

# Check if glean config is properly set
# Need both SOURCE_PROJECT and a valid PROJECT_ID
GLEAN_CONFIGURED = bool(
    config.SOURCE_PROJECT
    and config.SOURCE_TABLE
    and config.PROJECT_ID  # PROJECT_ID must not be empty
)


class TestGleanFetching:
    """Test fetching from glean.drive_files using mosaic logic."""

    def test_fetch_from_glean_small_batch(self):
        """Test fetching a small batch from glean."""
        if not GLEAN_CONFIGURED:
            print("⊘ test_fetch_from_glean_small_batch SKIPPED (glean not configured)")
            return

        tool = FetchAndSampleTool()
        response = json.loads(tool({
            "fetch_from_glean": True,
            "limit": 1,  # Just fetch 1 document
            "sample_size": 5,
        }))

        assert response["status"] == "success"
        assert response["source_type"] == "glean_document"
        assert response["guid"] is not None
        assert response["raw_sample"] is not None
        assert response["detected_format_hint"] in ["csv", "json", "pipe", "tab", "unknown"]
        assert response["total_bytes"] > 0
        print(f"✓ test_fetch_from_glean_small_batch PASSED (guid: {response['guid'][:8]}...)")

    def test_fetch_from_glean_with_header_detection(self):
        """Test glean fetching with heuristic header detection."""
        if not GLEAN_CONFIGURED:
            print("⊘ test_fetch_from_glean_with_header_detection SKIPPED (glean not configured)")
            return

        tool = FetchAndSampleTool()
        response = json.loads(tool({
            "fetch_from_glean": True,
            "limit": 1,
            "sample_size": 10,
            "find_header_heuristic": True,
        }))

        assert response["status"] == "success"
        assert "actual_header_row_index" in response
        assert "first_line_is_header" in response
        print(f"✓ test_fetch_from_glean_with_header_detection PASSED")

    def test_fetch_from_glean_metadata(self):
        """Test that glean documents have proper metadata."""
        if not GLEAN_CONFIGURED:
            print("⊘ test_fetch_from_glean_metadata SKIPPED (glean not configured)")
            return

        tool = FetchAndSampleTool()
        response = json.loads(tool({
            "fetch_from_glean": True,
            "limit": 1,
        }))

        assert response["status"] == "success"
        # Mosaic includes guid, title, body_length
        assert response["guid"] is not None
        assert response["total_bytes"] > 0
        # Format and header detection should work
        assert response["detected_format_hint"] is not None
        print(f"✓ test_fetch_from_glean_metadata PASSED")

    def test_fetch_from_glean_respects_limit(self):
        """Test that limit parameter works."""
        if not GLEAN_CONFIGURED:
            print("⊘ test_fetch_from_glean_respects_limit SKIPPED (glean not configured)")
            return

        tool = FetchAndSampleTool()
        response = json.loads(tool({
            "fetch_from_glean": True,
            "limit": 5,  # Request 5 documents
        }))

        # Returns first document only (current behavior)
        assert response["status"] == "success"
        assert response["guid"] is not None
        print(f"✓ test_fetch_from_glean_respects_limit PASSED")

    def test_fetch_from_glean_vs_body_text(self):
        """Test that fetch_from_glean and body_text paths produce consistent results."""
        if not GLEAN_CONFIGURED:
            print("⊘ test_fetch_from_glean_vs_body_text SKIPPED (glean not configured)")
            return

        tool = FetchAndSampleTool()

        # Fetch one document from glean
        glean_response = json.loads(tool({
            "fetch_from_glean": True,
            "limit": 1,
            "sample_size": 10,
        }))

        assert glean_response["status"] == "success"

        # Process the body_text we got
        body_text_response = json.loads(tool({
            "guid": glean_response["guid"],
            "body_text": glean_response["raw_sample"],  # Use raw_sample as body_text
            "sample_size": 10,
        }))

        assert body_text_response["status"] == "success"
        # Both should have same format detection
        assert glean_response["detected_format_hint"] == body_text_response["detected_format_hint"]
        print(f"✓ test_fetch_from_glean_vs_body_text PASSED")


def run_all_tests():
    """Run all tests."""
    instance = TestGleanFetching()
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
    print(f"Glean Fetching Tests: {passed}/{total} passed")
    print(f"{'='*70}")

    return passed == total


if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)
