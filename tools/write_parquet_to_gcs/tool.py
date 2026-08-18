"""Tool 6: write a passing extraction to GCS as Parquet.

One Parquet file per document, holding that document's own columns as real
Parquet columns. There is no shared schema across the corpus and no JSON blob:
a payroll sheet's file has the payroll sheet's columns, and the next file has
different ones.

    gs://<bucket>/<prefix>/guid=<guid>/<guid>.parquet

A document that carries more than one worksheet (extraction/core/records.py's
split_sheets(); rows tagged with "_sheet_name" by run_pipeline.py's merge)
gets one file *per sheet* instead of one wide file unioning every sheet's
columns into a mostly-NULL table, named for the sheet rather than the guid:

    gs://<bucket>/<prefix>/guid=<guid>/<sheet_slug>.parquet

Sheets are genuinely different tables - different headers, different meaning
- so forcing them into one schema just replaces "which columns does this row
actually have" with "which of these 160 columns are NULL for this row." Each
sheet's own file has only its own columns, and "_sheet_name" is dropped from
the row content once it's the filename itself. An ordinary single-sheet
document (the common case) still gets exactly the plain, guid-named path
above, unchanged.

Partitioned by guid (every sheet's file still sits under that document's own
guid= directory) and named for the sheet or the guid, deliberately without a
date. Writing to a fixed path per document/sheet makes a re-extraction
overwrite its predecessor, so the output is exactly-once by construction. A
date in the path would leave two files for a reprocessed document with
nothing to say which one counts — and unlike the BigQuery table this
replaced, there is no read-time dedup to fall back on. The extraction
timestamp is carried in the file's own metadata instead.

Every value is written as a string. The parser deliberately infers no types, so
typing them here would be inventing information the pipeline does not have.

Bookkeeping the parser emits keeps its underscore prefix (_row_number, _valid,
_errors), which is also what keeps it from colliding with a document that has a
column genuinely called "valid".
"""

import io
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

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

    SHEET_KEY = "_sheet_name"

    def blob_path(self, guid: str, sheet_name: Optional[str] = None) -> str:
        """The file is named for the sheet when there is one, and for the
        guid itself when there isn't (an ordinary single-table document, no
        sheet to name it after)."""
        filename = self._slug(sheet_name) if sheet_name is not None else guid
        return f"{self.prefix}/guid={guid}/{filename}.parquet"

    def uri(self, guid: str, sheet_name: Optional[str] = None) -> str:
        return f"gs://{self.bucket_name}/{self.blob_path(guid, sheet_name)}"

    @staticmethod
    def _slug(name: str) -> str:
        """A filesystem/URL-safe filename for a sheet name.

        Sheet names are the source workbook's own tab names verbatim, so they
        can carry commas, slashes, spaces or nothing usable at all - none of
        which belong in a GCS object path.
        """
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name or "").strip("_")
        return safe[:80] or "unnamed"

    def __call__(self, inputs: Dict[str, Any]) -> str:
        """Write one document, or a batch of them - one file per sheet.

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
            guids_written = set()

            for document in documents:
                guid = document.get("guid", "unknown")
                rows = document.get("extracted_rows") or []
                if not rows:
                    # Nothing to write is not a failure: a document can legitimately
                    # carry no data rows. Reported so the caller can tell the
                    # difference between that and a file it expected to find.
                    skipped.append(guid)
                    continue

                for sheet_name, sheet_rows in self._group_by_sheet(rows):
                    table = self._build_table(guid, sheet_rows, extracted_at, sheet_name)
                    size = self._upload(guid, table, sheet_name)

                    written.append({
                        "guid": guid,
                        "sheet_name": sheet_name,
                        "uri": self.uri(guid, sheet_name),
                        "rows": table.num_rows,
                        "columns": table.num_columns,
                        "bytes": size,
                    })
                    rows_total += table.num_rows
                    bytes_total += size
                guids_written.add(guid)

            if written:
                logger.info(
                    f"Wrote {len(written)} Parquet file(s) for {len(guids_written)} "
                    f"document(s), {rows_total} row(s), {bytes_total/1e6:.1f} MB to "
                    f"gs://{self.bucket_name}/{self.prefix}/"
                )

            return json.dumps({
                "status": "success",
                "bucket": self.bucket_name,
                "prefix": self.prefix,
                "documents_written": len(guids_written),
                "documents_empty": len(skipped),
                "files_written": len(written),
                "rows_written": rows_total,
                "bytes_written": bytes_total,
                "files": written,
                "empty_guids": skipped,
            }, indent=2)

        except Exception as e:
            logger.error(f"Parquet write failed: {e}")
            return json.dumps({"status": "error", "error": str(e)})

    def _group_by_sheet(
        self, rows: List[Dict[str, Any]]
    ) -> List[Tuple[Optional[str], List[Dict[str, Any]]]]:
        """Split one document's rows into its sheets, in first-seen order.

        A row without SHEET_KEY (the ordinary, single-table document) is its
        own group keyed None, so it takes the plain no-sheet path unchanged.
        SHEET_KEY itself is dropped from each row's own content - once it is
        the partition, repeating it as a constant column in every row of the
        file it names would be redundant.

        Rows and, transitively, each group's own column order are preserved
        exactly as they arrived - nothing here sorts or re-keys either one.
        """
        groups: Dict[Optional[str], List[Dict[str, Any]]] = {}
        order: List[Optional[str]] = []
        for row in rows:
            sheet_name = row.get(self.SHEET_KEY)
            if sheet_name not in groups:
                groups[sheet_name] = []
                order.append(sheet_name)
            groups[sheet_name].append(
                {k: v for k, v in row.items() if k != self.SHEET_KEY}
            )
        return [(name, groups[name]) for name in order]

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
        self, guid: str, rows: List[Dict[str, Any]], extracted_at: str,
        sheet_name: Optional[str] = None,
    ) -> pa.Table:
        """Turn one sheet's (or one whole single-table document's) rows into
        an Arrow table with only that sheet's own columns."""
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
            b"sheet_name": (sheet_name or "").encode(),
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

    def _upload(self, guid: str, table: pa.Table, sheet_name: Optional[str] = None) -> int:
        """Write one table to its document's (or sheet's) path, replacing
        what was there."""
        buffer = io.BytesIO()
        pq.write_table(table, buffer, compression=self.COMPRESSION)
        buffer.seek(0)

        blob = self._bucket.blob(self.blob_path(guid, sheet_name))
        blob.upload_from_file(buffer, content_type="application/octet-stream")
        return blob.size or buffer.getbuffer().nbytes

    def read_back(self, guid: str, sheet_name: Optional[str] = None) -> pa.Table:
        """Read a document's (or one sheet's) file back. For verification and tests."""
        data = self._bucket.blob(self.blob_path(guid, sheet_name)).download_as_bytes()
        return pq.read_table(io.BytesIO(data))
