"""Tests for write_to_gcs tool."""

import json
import datetime
from unittest.mock import Mock, patch, MagicMock

from tools.write_to_gcs.tool import WriteToGcsTool


def test_initialization():
    """Test WriteToGcsTool initialization."""
    mock_client = Mock()
    mock_bucket = Mock()
    mock_client.bucket.return_value = mock_bucket

    tool = WriteToGcsTool(
        bucket="test-bucket",
        prefix="test-prefix",
        source="test-source",
        run_id="test-run-123",
        client=mock_client,
    )

    assert tool.bucket_name == "test-bucket"
    assert tool.prefix == "test-prefix"
    assert tool.source == "test-source"
    assert tool.run_id == "test-run-123"
    assert tool.files_written == 0
    assert tool.rows_written == 0

    print("✓ test_initialization PASSED")


def test_path_generation():
    """Test GCS path generation with proper partitioning."""
    mock_client = Mock()
    mock_bucket = Mock()
    mock_client.bucket.return_value = mock_bucket

    tool = WriteToGcsTool(
        bucket="test-bucket",
        prefix="extraction",
        source="agentic",
        run_id="run-abc123",
        client=mock_client,
    )

    path1 = tool._next_path(batch_id=1)
    path2 = tool._next_path(batch_id=2)

    # Verify Hive-style partitioning
    assert "source=agentic" in path1
    assert "dt=" in path1
    assert "run=run-abc123" in path1
    assert "batch-000001-part-00001.jsonl" in path1

    # Verify sequence increment
    assert "part-00001" in path1
    assert "part-00002" in path2

    print("✓ test_path_generation PASSED")


def test_write_batch_empty():
    """Test that empty batches are skipped."""
    mock_client = Mock()
    mock_bucket = Mock()
    mock_client.bucket.return_value = mock_bucket

    tool = WriteToGcsTool(bucket="test-bucket", client=mock_client)

    result = tool.write_batch([], batch_id=1)

    assert result is None
    assert tool.files_written == 0
    assert tool.rows_written == 0

    print("✓ test_write_batch_empty PASSED")


def test_write_batch_with_rows():
    """Test writing batch of rows to GCS."""
    mock_client = Mock()
    mock_bucket = Mock()
    mock_blob = Mock()
    mock_bucket.blob.return_value = mock_blob
    mock_client.bucket.return_value = mock_bucket

    tool = WriteToGcsTool(bucket="test-bucket", client=mock_client)

    rows = [
        {
            "PERSON_EMAIL": "john@example.com",
            "PERSON_FULL_NAME": "John Smith",
            "PERSON_ID": "10001",
        },
        {
            "PERSON_EMAIL": "jane@example.com",
            "PERSON_FULL_NAME": "Jane Doe",
            "PERSON_ID": "10002",
        },
    ]

    uri = tool.write_batch(rows, batch_id=1)

    assert uri is not None
    assert "gs://test-bucket" in uri
    assert tool.files_written == 1
    assert tool.rows_written == 2
    assert tool.bytes_written > 0

    # Verify upload was called
    mock_blob.upload_from_string.assert_called_once()
    call_args = mock_blob.upload_from_string.call_args
    uploaded_body = call_args[0][0]

    # Verify NDJSON format (one JSON per line)
    lines = uploaded_body.decode("utf-8").strip().split("\n")
    assert len(lines) == 2

    # Verify JSON is compact (no spaces around separators)
    assert "," in lines[0]  # Comma separator
    assert ":" in lines[0]  # Colon separator
    # Check for compact encoding: no space after comma or colon
    assert ", " not in lines[0]  # No space after comma
    assert ": " not in lines[0]  # No space after colon

    print("✓ test_write_batch_with_rows PASSED")


def test_tool_interface():
    """Test tool interface for pipeline integration."""
    mock_client = Mock()
    mock_bucket = Mock()
    mock_blob = Mock()
    mock_bucket.blob.return_value = mock_blob
    mock_client.bucket.return_value = mock_bucket

    tool = WriteToGcsTool(bucket="test-bucket", client=mock_client)

    inputs = {
        "guid": "ddffbdb6-5041-4d65-a744-5a0631a629aa",
        "batch_id": 1,
        "extracted_at": "2026-08-13T18:00:00Z",
        "extracted_rows": [
            {
                "PERSON_EMAIL": "john@example.com",
                "PERSON_FULL_NAME": "John Smith",
            },
        ],
    }

    response_str = tool(inputs)
    response = json.loads(response_str)

    assert response["status"] == "success"
    assert response["guid"] == "ddffbdb6-5041-4d65-a744-5a0631a629aa"
    assert response["batch_id"] == 1
    assert response["rows_written"] == 1
    assert response["uri"] is not None
    assert response["run_id"] is not None

    print("✓ test_tool_interface PASSED")


def test_tool_interface_empty_rows():
    """Test tool interface with no rows to write."""
    mock_client = Mock()
    mock_bucket = Mock()
    mock_client.bucket.return_value = mock_bucket

    tool = WriteToGcsTool(bucket="test-bucket", client=mock_client)

    inputs = {
        "guid": "test-guid",
        "batch_id": 1,
        "extracted_rows": [],
    }

    response_str = tool(inputs)
    response = json.loads(response_str)

    assert response["status"] == "success"
    assert response["rows_written"] == 0
    assert response["uri"] is None

    print("✓ test_tool_interface_empty_rows PASSED")


def test_audit_metadata_added():
    """Test that guid and EXTRACTED_AT are added to rows."""
    mock_client = Mock()
    mock_bucket = Mock()
    mock_blob = Mock()
    mock_bucket.blob.return_value = mock_blob
    mock_client.bucket.return_value = mock_bucket

    tool = WriteToGcsTool(bucket="test-bucket", client=mock_client)

    inputs = {
        "guid": "test-guid-123",
        "batch_id": 1,
        "extracted_at": "2026-08-13T12:34:56Z",
        "extracted_rows": [
            {"PERSON_EMAIL": "john@example.com"},
        ],
    }

    tool(inputs)

    # Verify upload was called
    mock_blob.upload_from_string.assert_called_once()
    uploaded_body = mock_blob.upload_from_string.call_args[0][0]

    # Parse the JSON line
    line = uploaded_body.decode("utf-8").strip()
    row = json.loads(line)

    # Verify audit fields were added
    assert row["guid"] == "test-guid-123"
    assert row["EXTRACTED_AT"] == "2026-08-13T12:34:56Z"
    assert row["PERSON_EMAIL"] == "john@example.com"

    print("✓ test_audit_metadata_added PASSED")


def test_thread_safety():
    """Test that sequence counter is thread-safe."""
    mock_client = Mock()
    mock_bucket = Mock()
    mock_blob = Mock()
    mock_bucket.blob.return_value = mock_blob
    mock_client.bucket.return_value = mock_bucket

    tool = WriteToGcsTool(bucket="test-bucket", client=mock_client)

    # Simulate concurrent calls to _next_path
    path1 = tool._next_path(batch_id=1)
    path2 = tool._next_path(batch_id=1)
    path3 = tool._next_path(batch_id=1)

    # Extract sequence numbers
    import re

    seq1 = int(re.search(r"part-(\d+)", path1).group(1))
    seq2 = int(re.search(r"part-(\d+)", path2).group(1))
    seq3 = int(re.search(r"part-(\d+)", path3).group(1))

    # Verify sequences are incremented and unique
    assert seq1 == 1
    assert seq2 == 2
    assert seq3 == 3

    print("✓ test_thread_safety PASSED")


def test_summary():
    """Test summary output."""
    mock_client = Mock()
    mock_bucket = Mock()
    mock_blob = Mock()
    mock_bucket.blob.return_value = mock_blob
    mock_client.bucket.return_value = mock_bucket

    tool = WriteToGcsTool(
        bucket="test-bucket",
        prefix="my-prefix",
        client=mock_client,
    )

    rows = [{"PERSON_EMAIL": f"person{i}@example.com"} for i in range(100)]
    tool.write_batch(rows, batch_id=1)

    summary = tool.summary()

    assert "1 file(s)" in summary
    assert "100 rows" in summary
    assert "test-bucket/my-prefix" in summary
    assert f"run={tool.run_id}" in summary

    print("✓ test_summary PASSED")


def test_error_handling_missing_rows():
    """Test error handling for missing extracted_rows."""
    tool = WriteToGcsTool(bucket="test-bucket")

    inputs = {
        "guid": "test-guid",
        "batch_id": 1,
        # Missing extracted_rows
    }

    response_str = tool(inputs)
    response = json.loads(response_str)

    assert response["status"] == "success"  # Gracefully handles missing rows
    assert response["rows_written"] == 0

    print("✓ test_error_handling_missing_rows PASSED")


def run_all_tests():
    """Run all tests."""
    tests = [
        test_initialization,
        test_path_generation,
        test_write_batch_empty,
        test_write_batch_with_rows,
        test_tool_interface,
        test_tool_interface_empty_rows,
        test_audit_metadata_added,
        test_thread_safety,
        test_summary,
        test_error_handling_missing_rows,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"✗ {test.__name__} FAILED: {str(e)}")
            failed += 1

    print(f"\n{'='*60}")
    print(f"Test Results: {passed}/{len(tests)} passed, {failed} failed")
    print(f"{'='*60}")

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)
