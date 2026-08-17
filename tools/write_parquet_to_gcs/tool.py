"""Tool 6: write a passing extraction to GCS as Parquet.

One Parquet file per document, holding that document's own columns as real
Parquet columns. There is no shared schema across the corpus and no JSON blob:
a payroll sheet's file has the payroll sheet's columns, and the next file has
different ones.

    gs://<bucket>/<prefix>/guid=<guid>/part-0000.parquet

Partitioned by guid alone, deliberately without a date. Writing to a fixed path
per document makes a re-extraction overwrite its predecessor, so the output is
exactly-once by construction. A date in the path would leave two files for a
reprocessed document with nothing to say which one counts — and unlike the
BigQuery table this replaced, there is no read-time dedup to fall back on.
The extraction timestamp is carried in the file's own metadata instead.

Every value is written as a string. The parser deliberately infers no types, so
typing them here would be inventing information the pipeline does not have.

Bookkeeping the parser emits keeps its underscore prefix (_row_number, _valid,
_errors), which is also what keeps it from colliding with a document that has a
column genuinely called "valid".
"""

import io
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pyarrow as pa
import pyarrow.parquet as pq
from google.cloud import storage

from extraction.core import config

logger = logging.getLogger(__name__)


class WriteParquetToGcsTool:
    """Write extracted rows to GCS, one Parquet file per document."""

    name = "write_parquet_to_gcs"
    description = "Write a passing extraction to GCS as one Parquet file per document"

    ROW_NUMBER_KEY = "_row_number"
    VALID_KEY = "_valid"
    ERRORS_KEY = "_errors"
    EXTRA_KEY = "_extra_values"

    # Types for the parser's own bookkeeping. Everything else is a string.
    BOOKKEEPING_TYPES = {
        ROW_NUMBER_KEY: pa.int64(),
        VALID_KEY: pa.bool_(),
        ERRORS_KEY: pa.list_(pa.string()),
        EXTRA_KEY: pa.int64(),
    }

    COMPRESSION = "snappy"

    def __init__(
        self,
        client: storage.Client = None,
        bucket: str = None,
        prefix: str = None,
        extraction_version: str = "agentic-v1",
    ):
        self.bucket_name = bucket or config.GCS_OUTPUT_BUCKET
        self.prefix = (prefix or config.GCS_OUTPUT_PREFIX).strip("/")
        self.project = config.PROJECT_ID
        self.extraction_version = extraction_version

        if not self.bucket_name:
            raise ValueError(
                "No output bucket. Set GCS_OUTPUT_BUCKET before writing Parquet."
            )
        if not self.project:
            raise ValueError("No project configured. Set PROJECT_ID.")

        self._client = client or storage.Client(project=self.project)
        # client.bucket() makes no API call. get_bucket()/exists() would, and the
        # pipeline's service account is granted object access without
        # storage.buckets.get — checking the bucket would fail where writing to
        # it succeeds.
        self._bucket = self._client.bucket(self.bucket_name)

    def blob_path(self, guid: str) -> str:
        return f"{self.prefix}/guid={guid}/part-0000.parquet"

    def uri(self, guid: str) -> str:
        return f"gs://{self.bucket_name}/{self.blob_path(guid)}"

    def __call__(self, inputs: Dict[str, Any]) -> str:
        """Write one document, or a batch of them, as one file each.

        Args:
            inputs: either
                {"guid": ..., "extracted_rows": [...]}                  one document
                {"documents": [{"guid": ..., "extracted_rows": [...]}]} many

        Returns:
            JSON string with the files written, and their rows and bytes
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

            written: List[Dict[str, Any]] = []
            skipped: List[str] = []
            rows_total = bytes_total = 0

            for document in documents:
                guid = document.get("guid", "unknown")
                rows = document.get("extracted_rows") or []
                if not rows:
                    # Nothing to write is not a failure: a document can legitimately
                    # carry no data rows. Reported so the caller can tell the
                    # difference between that and a file it expected to find.
                    skipped.append(guid)
                    continue

                table = self._build_table(guid, rows, extracted_at)
                size = self._upload(guid, table)

                written.append({
                    "guid": guid,
                    "uri": self.uri(guid),
                    "rows": table.num_rows,
                    "columns": table.num_columns,
                    "bytes": size,
                })
                rows_total += table.num_rows
                bytes_total += size

            if written:
                logger.info(
                    f"Wrote {len(written)} Parquet file(s), {rows_total} row(s), "
                    f"{bytes_total/1e6:.1f} MB to gs://{self.bucket_name}/{self.prefix}/"
                )

            return json.dumps({
                "status": "success",
                "bucket": self.bucket_name,
                "prefix": self.prefix,
                "documents_written": len(written),
                "documents_empty": len(skipped),
                "rows_written": rows_total,
                "bytes_written": bytes_total,
                "files": written,
                "empty_guids": skipped,
            }, indent=2)

        except Exception as e:
            logger.error(f"Parquet write failed: {e}")
            return json.dumps({"status": "error", "error": str(e)})

    def _ordered_columns(self, rows: List[Dict[str, Any]]) -> List[str]:
        """Column order for the file: first appearance across every row.

        Taken across all rows rather than from the first one because a row that
        overflowed its declared width carries _extra_values while its neighbours
        do not, and a column present in only some rows still belongs in the file.
        """
        columns: List[str] = []
        seen = set()
        for row in rows:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    columns.append(key)
        return columns

    def _build_table(
        self, guid: str, rows: List[Dict[str, Any]], extracted_at: str
    ) -> pa.Table:
        """Turn one document's rows into an Arrow table with its own columns."""
        columns = self._ordered_columns(rows)
        arrays = []
        fields = []

        for column in columns:
            arrow_type = self.BOOKKEEPING_TYPES.get(column, pa.string())
            values = [self._coerce(row.get(column), arrow_type) for row in rows]
            arrays.append(pa.array(values, type=arrow_type))
            fields.append(pa.field(column, arrow_type))

        # Carried in the file rather than in the path, so a re-extraction can
        # overwrite the file without losing when it was produced.
        schema = pa.schema(fields, metadata={
            b"guid": guid.encode(),
            b"extracted_at": extracted_at.encode(),
            b"extraction_version": self.extraction_version.encode(),
            b"row_count": str(len(rows)).encode(),
            b"column_count": str(
                len([c for c in columns if not c.startswith("_")])
            ).encode(),
        })
        return pa.Table.from_arrays(arrays, schema=schema)

    @staticmethod
    def _coerce(value: Any, arrow_type: pa.DataType) -> Any:
        """Fit one value to its column's type, leaving absent values absent."""
        if value is None:
            return None
        if arrow_type == pa.int64():
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
        if arrow_type == pa.bool_():
            return bool(value)
        if arrow_type == pa.list_(pa.string()):
            if isinstance(value, str):
                return [value]
            return [str(v) for v in value]
        if isinstance(value, (list, dict)):
            # A parser that produced a structure where a cell was expected: keep
            # it verbatim rather than dropping it.
            return json.dumps(value, default=str)
        return str(value)

    def _upload(self, guid: str, table: pa.Table) -> int:
        """Write one table to its document's path, replacing what was there."""
        buffer = io.BytesIO()
        pq.write_table(table, buffer, compression=self.COMPRESSION)
        buffer.seek(0)

        blob = self._bucket.blob(self.blob_path(guid))
        blob.upload_from_file(buffer, content_type="application/octet-stream")
        return blob.size or buffer.getbuffer().nbytes

    def read_back(self, guid: str) -> pa.Table:
        """Read a document's file back. For verification and tests."""
        data = self._bucket.blob(self.blob_path(guid)).download_as_bytes()
        return pq.read_table(io.BytesIO(data))
