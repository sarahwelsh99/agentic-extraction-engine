"""Tests for load_to_bigquery (Tool 6).

The BigQuery client is stubbed. What is pinned here is the record shape and the
batching, since a malformed record or a job per document is what breaks a load,
and neither needs a real dataset to test.
"""

import json
from google.cloud import bigquery
from tools.load_to_bigquery.tool import LoadToBigQueryTool


class _StubClient:
    """Captures what would have been sent to BigQuery."""

    def __init__(self, table_exists=True):
        self.loaded = None
        self.table_id = None
        self.job_config = None
        self.created_tables = []
        self.jobs = 0
        self._table_exists = table_exists

    def get_dataset(self, dataset_id):
        return object()

    def create_dataset(self, dataset_id, exists_ok=False):
        pass

    def get_table(self, table_id):
        if not self._table_exists:
            raise Exception("not found")
        return object()

    def create_table(self, table, exists_ok=False):
        self.created_tables.append(table)
        return table

    def load_table_from_json(self, payload, table_id, job_config=None):
        self.loaded = payload
        self.table_id = table_id
        self.job_config = job_config
        self.jobs += 1

        class _Job:
            def result(self_inner):
                return None

        return _Job()


def _loader(client=None, **kw):
    import extraction.core.config as config
    config.PROJECT_ID = config.PROJECT_ID or "test-project"
    return LoadToBigQueryTool(client=client or _StubClient(), **kw)


ROWS = [
    {"id": "1", "name": "Adam", "email": "adam@x.com",
     "_valid": True, "_row_number": 2},
    {"id": "2", "name": None, "email": "jane@x.com",
     "_valid": False, "_errors": ["could not read field 2"], "_row_number": 3},
]


def test_columns_go_into_the_json_column():
    """A document's own columns live in data, not in the table schema."""
    client = _StubClient()
    _loader(client)({"guid": "abc-123", "extracted_rows": ROWS})

    record = client.loaded[0]
    assert record["guid"] == "abc-123"
    assert record["data"] == {"id": "1", "name": "Adam", "email": "adam@x.com"}
    assert record["column_count"] == 3
    # bookkeeping is lifted out of the JSON into real columns
    assert "_valid" not in record["data"] and "_row_number" not in record["data"]
    assert record["row_number"] == 2
    assert record["valid"] is True

    print("✓ test_columns_go_into_the_json_column PASSED")


def test_errors_are_kept_as_a_repeated_column():
    client = _StubClient()
    _loader(client)({"guid": "g", "extracted_rows": ROWS})

    second = client.loaded[1]
    assert second["valid"] is False
    assert second["errors"] == ["could not read field 2"]
    # a null value is preserved rather than dropped from data
    assert second["data"]["name"] is None

    print("✓ test_errors_are_kept_as_a_repeated_column PASSED")


def test_many_documents_load_in_one_job():
    """The whole point: the load quota counts jobs, not rows or documents."""
    client = _StubClient()
    r = json.loads(_loader(client)({
        "documents": [
            {"guid": f"doc-{i}", "extracted_rows": ROWS} for i in range(500)
        ]
    }))

    assert client.jobs == 1, "500 documents must not mean 500 load jobs"
    assert r["documents_loaded"] == 500
    assert r["rows_loaded"] == 1000
    assert r["load_jobs"] == 1

    print("✓ test_many_documents_load_in_one_job PASSED")


def test_single_document_form_still_works():
    """The one-guid CLI path passes a single document, not a batch."""
    client = _StubClient()
    r = json.loads(_loader(client)({"guid": "solo", "extracted_rows": ROWS}))

    assert r["documents_loaded"] == 1
    assert r["rows_loaded"] == 2
    assert {rec["guid"] for rec in client.loaded} == {"solo"}

    print("✓ test_single_document_form_still_works PASSED")


def test_table_is_partitioned_and_clustered_on_creation():
    """Clustering by guid is what keeps reading one document cheap.

    Creation is opt-in: the pipeline expects the table to have been provisioned
    by scripts/provision_extraction_table.py, so that a drifting schema cannot
    appear on whichever machine happened to load first.
    """
    client = _StubClient(table_exists=False)
    _loader(client, create_if_missing=True)({"guid": "g", "extracted_rows": ROWS})

    assert client.created_tables, "table should have been created"
    table = client.created_tables[0]
    assert table.clustering_fields == ["guid"]
    assert table.time_partitioning.field == "extracted_at"

    print("✓ test_table_is_partitioned_and_clustered_on_creation PASSED")


def test_load_appends_rather_than_truncates():
    """The table holds the whole corpus; a load must not replace it."""
    client = _StubClient()
    _loader(client)({"guid": "g", "extracted_rows": ROWS})

    assert client.job_config.write_disposition == "WRITE_APPEND"

    print("✓ test_load_appends_rather_than_truncates PASSED")


def test_values_are_rendered_as_strings():
    """The parser infers no types, so the loader must not invent them."""
    client = _StubClient()
    _loader(client)({
        "guid": "g",
        "extracted_rows": [{"n": 42, "tags": ["a", "b"], "_valid": True}],
    })

    data = client.loaded[0]["data"]
    assert data["n"] == "42"
    assert json.loads(data["tags"]) == ["a", "b"]

    print("✓ test_values_are_rendered_as_strings PASSED")


def test_missing_table_is_refused_rather_than_created():
    """A missing table is an error naming the provisioning script, not a create."""
    client = _StubClient(table_exists=False)
    r = json.loads(_loader(client)({"guid": "g", "extracted_rows": ROWS}))

    assert r["status"] == "error"
    assert "provision_extraction_table" in r["error"]
    assert not client.created_tables

    print("\u2713 test_missing_table_is_refused_rather_than_created PASSED")


def test_no_rows_is_a_quiet_no_op():
    client = _StubClient()
    r = json.loads(_loader(client)({"guid": "g", "extracted_rows": []}))

    assert r["status"] == "success"
    assert r["rows_loaded"] == 0
    assert client.jobs == 0, "nothing should have been sent"

    print("✓ test_no_rows_is_a_quiet_no_op PASSED")


def run_all_tests():
    tests = [
        test_columns_go_into_the_json_column,
        test_errors_are_kept_as_a_repeated_column,
        test_many_documents_load_in_one_job,
        test_single_document_form_still_works,
        test_table_is_partitioned_and_clustered_on_creation,
        test_load_appends_rather_than_truncates,
        test_values_are_rendered_as_strings,
        test_missing_table_is_refused_rather_than_created,
        test_no_rows_is_a_quiet_no_op,
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
