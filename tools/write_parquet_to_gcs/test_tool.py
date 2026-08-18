"""Tests for write_parquet_to_gcs (Tool 6).

The GCS client is stubbed; what is pinned here is the file layout (one
Parquet file per document, its own columns, guid-partitioned path) and the
values written into it, since neither needs a real bucket to test.
"""

import io
import json
import pyarrow.parquet as pq
from tools.write_parquet_to_gcs.tool import WriteParquetToGcsTool


class _StubBlob:
    def __init__(self, path):
        self.path = path
        self.data = None
        self.size = 0

    def upload_from_file(self, file_obj, content_type=None):
        self.data = file_obj.read()
        self.size = len(self.data)

    def download_as_bytes(self):
        return self.data


class _StubBucket:
    def __init__(self):
        self.blobs = {}

    def blob(self, path):
        return self.blobs.setdefault(path, _StubBlob(path))


class _StubClient:
    def __init__(self):
        self._bucket = _StubBucket()

    def bucket(self, name):
        return self._bucket


def _writer(client=None, **kw):
    import extraction.core.config as config
    config.PROJECT_ID = config.PROJECT_ID or "test-project"
    kw.setdefault("bucket", "test-bucket")
    kw.setdefault("prefix", "test")
    return WriteParquetToGcsTool(client=client or _StubClient(), **kw)


ROWS = [
    {"id": "1", "name": "Adam", "balance": 42, "_valid": True, "_row_number": 2},
    {"id": "2", "name": None, "balance": 7, "_valid": False,
     "_errors": ["could not read field 2"], "_row_number": 3},
]


def test_document_gets_its_own_parquet_file_with_typed_bookkeeping():
    """A document's own columns are written verbatim as strings; the parser's
    bookkeeping columns (_row_number, _valid, _errors) get real types."""
    client = _StubClient()
    r = json.loads(_writer(client)({"guid": "abc-123", "extracted_rows": ROWS}))

    assert r["status"] == "success", r
    assert r["documents_written"] == 1
    assert r["rows_written"] == 2

    table = pq.read_table(io.BytesIO(client._bucket.blobs["test/guid=abc-123/part-0000.parquet"].data))
    row0 = table.to_pylist()[0]
    assert row0["id"] == "1" and row0["balance"] == "42", "values are strings, never inferred"
    assert row0["_row_number"] == 2 and row0["_valid"] is True

    print("✓ test_document_gets_its_own_parquet_file_with_typed_bookkeeping PASSED")


def test_many_documents_write_one_file_each():
    """Unlike the BigQuery table this replaced, there is no shared schema:
    each document's file holds only that document's own columns."""
    client = _StubClient()
    r = json.loads(_writer(client)({
        "documents": [
            {"guid": "doc-a", "extracted_rows": [{"a": "1", "_valid": True, "_row_number": 1}]},
            {"guid": "doc-b", "extracted_rows": [{"b": "2", "c": "3", "_valid": True, "_row_number": 1}]},
        ]
    }))

    assert r["documents_written"] == 2
    assert r["rows_written"] == 2
    assert len(client._bucket.blobs) == 2
    assert {f["guid"] for f in r["files"]} == {"doc-a", "doc-b"}

    print("✓ test_many_documents_write_one_file_each PASSED")


def test_empty_document_is_skipped_not_failed():
    client = _StubClient()
    r = json.loads(_writer(client)({"guid": "empty-doc", "extracted_rows": []}))

    assert r["status"] == "success"
    assert r["documents_written"] == 0
    assert r["documents_empty"] == 1
    assert r["empty_guids"] == ["empty-doc"]
    assert not client._bucket.blobs, "nothing should have been written"

    print("✓ test_empty_document_is_skipped_not_failed PASSED")


def test_path_is_guid_partitioned_so_reextraction_overwrites():
    """A fixed path per document, with no date, is what makes a re-extraction
    overwrite rather than accumulate a second generation."""
    tool = _writer()
    assert tool.blob_path("g1") == "test/guid=g1/part-0000.parquet"
    assert tool.uri("g1") == "gs://test-bucket/test/guid=g1/part-0000.parquet"

    client = _StubClient()
    writer = _writer(client)
    writer({"guid": "g1", "extracted_rows": [{"a": "1", "_valid": True, "_row_number": 1}]})
    first = client._bucket.blobs["test/guid=g1/part-0000.parquet"].data
    writer({"guid": "g1", "extracted_rows": [{"a": "2", "_valid": True, "_row_number": 1}]})
    second = client._bucket.blobs["test/guid=g1/part-0000.parquet"].data

    assert first != second, "the second write must replace the file, not add to it"
    assert len(client._bucket.blobs) == 1

    print("✓ test_path_is_guid_partitioned_so_reextraction_overwrites PASSED")


def run_all_tests():
    tests = [
        test_document_gets_its_own_parquet_file_with_typed_bookkeeping,
        test_many_documents_write_one_file_each,
        test_empty_document_is_skipped_not_failed,
        test_path_is_guid_partitioned_so_reextraction_overwrites,
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
