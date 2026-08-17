"""Tool 6: load extractions into one BigQuery table.

Every document's rows go into a single table, with that document's own columns
carried in a JSON column. Each document keeps its own shape without needing its
own table, and one load job can carry many documents.

That last point is the reason for the design. BigQuery's load quota counts jobs,
not rows, so a table per document means a job per document — at corpus scale
that is the binding constraint long before storage or bytes are. Loading a batch
of documents in one job removes it.

The table is partitioned by extraction date and clustered by guid, so reading a
single document stays cheap while reading across the corpus stays possible.

Loads append. Re-extracting a document therefore leaves two generations of its
rows, distinguished by extracted_at. That is deliberate: deleting first would
cost a DML statement per document, which is the per-document job cost this
design exists to avoid. Read the latest generation with

    SELECT * FROM `<table>`
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY guid, row_number ORDER BY extracted_at DESC
    ) = 1

or call delete_document() first when reprocessing a single document by hand.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from google.cloud import bigquery

from extraction.core import config

logger = logging.getLogger(__name__)

DEFAULT_DATASET = "glean_extract"
DEFAULT_TABLE = "glean_structured_extraction"


class LoadToBigQueryTool:
    """Load extracted rows into one JSON-column table."""

    name = "load_to_bigquery"
    description = "Load extracted rows into a single BigQuery table with a JSON column"

    # Keys the parser adds for its own bookkeeping. Lifted into real columns
    # rather than left inside the JSON, so they can be filtered on cheaply.
    ROW_NUMBER_KEY = "_row_number"
    VALID_KEY = "_valid"
    ERRORS_KEY = "_errors"
    BOOKKEEPING = (ROW_NUMBER_KEY, VALID_KEY, ERRORS_KEY, "_extra_values")

    SCHEMA = [
        bigquery.SchemaField(
            "guid", "STRING", mode="REQUIRED",
            description="Source document, from glean.drive_files",
        ),
        bigquery.SchemaField(
            "row_number", "INTEGER",
            description="Position of this row within the source document",
        ),
        bigquery.SchemaField(
            "valid", "BOOLEAN",
            description="Whether the generated parser read this row cleanly",
        ),
        bigquery.SchemaField(
            "errors", "STRING", mode="REPEATED",
            description="Parser errors for this row, if any",
        ),
        bigquery.SchemaField(
            "column_count", "INTEGER",
            description="Number of columns carried in data",
        ),
        bigquery.SchemaField(
            "data", "JSON",
            description="The document's own columns and values for this row",
        ),
        bigquery.SchemaField(
            "extracted_at", "TIMESTAMP", mode="REQUIRED",
            description="When this row was extracted; the partitioning column",
        ),
    ]

    def __init__(
        self,
        client: bigquery.Client = None,
        dataset: str = None,
        table: str = None,
        create_if_missing: bool = False,
    ):
        self.dataset = dataset or os.environ.get("EXTRACTION_DATASET", DEFAULT_DATASET)
        self.table = table or os.environ.get("EXTRACTION_TABLE", DEFAULT_TABLE)
        self.project = config.PROJECT_ID
        if not self.project:
            raise ValueError(
                "No project configured. Set PROJECT_ID before loading to BigQuery."
            )
        # Provisioning is a deliberate step, run once (scripts/provision_extraction_table.py).
        # Creating the table implicitly here would let a drifting schema appear
        # silently on whichever machine happened to load first.
        self.create_if_missing = create_if_missing
        self._client = client or bigquery.Client(project=self.project)

    @property
    def table_id(self) -> str:
        return f"{self.project}.{self.dataset}.{self.table}"

    def __call__(self, inputs: Dict[str, Any]) -> str:
        """Load one document, or a batch of them, in a single job.

        Args:
            inputs: either
                {"guid": ..., "extracted_rows": [...]}                 one document
                {"documents": [{"guid": ..., "extracted_rows": [...]}]} many

        Returns:
            JSON string with the table written and the row count
        """
        try:
            documents = inputs.get("documents")
            if documents is None:
                documents = [{
                    "guid": inputs.get("guid", "unknown"),
                    "extracted_rows": inputs.get("extracted_rows") or [],
                }]

            extracted_at = inputs.get("extracted_at") or datetime.now(
                timezone.utc
            ).isoformat()

            payload = []
            for document in documents:
                guid = document.get("guid", "unknown")
                for row in document.get("extracted_rows") or []:
                    payload.append(self._as_record(guid, row, extracted_at))

            if not payload:
                return json.dumps({
                    "status": "success",
                    "table": self.table_id,
                    "documents_loaded": 0,
                    "rows_loaded": 0,
                    "message": "No rows to load",
                })

            self._ensure_table()

            job = self._client.load_table_from_json(
                payload,
                self.table_id,
                job_config=bigquery.LoadJobConfig(
                    schema=self.SCHEMA,
                    # Append: the table holds the whole corpus, so a load adds
                    # to it. Re-runs leave a second generation, resolved at read
                    # time by extracted_at (see the module docstring).
                    write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
                ),
            )
            job.result()

            guids = {d.get("guid", "unknown") for d in documents}
            logger.info(
                f"Loaded {len(payload)} rows from {len(guids)} document(s) "
                f"to {self.table_id}"
            )

            return json.dumps({
                "status": "success",
                "table": self.table_id,
                "documents_loaded": len(guids),
                "rows_loaded": len(payload),
                "load_jobs": 1,
            }, indent=2)

        except Exception as e:
            logger.error(f"BigQuery load failed: {e}")
            return json.dumps({"status": "error", "error": str(e)})

    def delete_document(self, guid: str) -> int:
        """Remove a document's rows, so a reprocessed document is not duplicated.

        Returns:
            Number of rows deleted
        """
        query = f"DELETE FROM `{self.table_id}` WHERE guid = @guid"
        job = self._client.query(
            query,
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("guid", "STRING", guid)
                ]
            ),
        )
        job.result()
        return job.num_dml_affected_rows or 0

    def _ensure_table(self) -> None:
        """Check the target exists, creating it only if asked to.

        Raises:
            RuntimeError: if the table is missing and create_if_missing is False
        """
        if not self.create_if_missing:
            try:
                self._client.get_table(self.table_id)
                return
            except Exception:
                raise RuntimeError(
                    f"{self.table_id} does not exist. Create it with "
                    f"scripts/provision_extraction_table.py, or pass "
                    f"create_if_missing=True."
                )

        dataset_id = f"{self.project}.{self.dataset}"
        try:
            self._client.get_dataset(dataset_id)
        except Exception:
            logger.info(f"Creating dataset {dataset_id}")
            self._client.create_dataset(dataset_id, exists_ok=True)

        try:
            self._client.get_table(self.table_id)
        except Exception:
            logger.info(f"Creating table {self.table_id}")
            table = bigquery.Table(self.table_id, schema=self.SCHEMA)
            table.time_partitioning = bigquery.TimePartitioning(
                type_=bigquery.TimePartitioningType.DAY,
                field="extracted_at",
            )
            table.clustering_fields = ["guid"]
            table.description = (
                "Rows extracted from glean structured records. Each document's "
                "own columns are carried in the JSON 'data' column."
            )
            self._client.create_table(table, exists_ok=True)

    def _as_record(
        self, guid: str, row: Dict[str, Any], extracted_at: str
    ) -> Dict[str, Any]:
        """Turn one extracted row into a table record.

        The document's columns go into 'data' as JSON. Values are rendered as
        strings because the parser deliberately infers no types, so claiming
        them here would be inventing them.
        """
        data = {}
        for key, value in row.items():
            if key in self.BOOKKEEPING:
                continue
            if value is None:
                data[key] = None
            elif isinstance(value, (list, dict)):
                data[key] = json.dumps(value, default=str)
            else:
                data[key] = str(value)

        errors = row.get(self.ERRORS_KEY) or []
        if isinstance(errors, str):
            errors = [errors]

        row_number = row.get(self.ROW_NUMBER_KEY)
        return {
            "guid": guid,
            "row_number": int(row_number) if str(row_number or "").isdigit() else None,
            "valid": bool(row.get(self.VALID_KEY, True)),
            "errors": [str(e)[:1024] for e in errors][:20],
            "column_count": len(data),
            "data": data,
            "extracted_at": extracted_at,
        }
