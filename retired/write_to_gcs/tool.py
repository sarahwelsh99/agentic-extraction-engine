"""Tool 5: Write extraction results to GCS as NDJSON.

Writes extracted rows as newline-delimited JSON to GCS with Hive-style partitioning.
Follows mosaic-glean-extraction's output_store.py pattern for reliability and scalability.

Input: Extracted rows from Tool 4 + metadata (guid, batch_id)
Output: GCS NDJSON files with audit trail + metadata
"""

import datetime
import json
import logging
import threading
import uuid
from typing import Any, Dict, List, Optional

from google.cloud import storage

from extraction.core import config

logger = logging.getLogger(__name__)


class WriteToGcsTool:
    """Writes extraction results as NDJSON to GCS.

    Follows mosaic-glean-extraction's GcsJsonOutput pattern:
    - Hive-style partitioning (source=, dt=, run=)
    - Thread-safe batch writing
    - Idempotent uploads (retries overwrite, no duplicates)
    - Audit metadata for every batch
    """

    name = "write_to_gcs"
    description = "Write extraction results to GCS as NDJSON with Hive-style partitioning"

    def __init__(
        self,
        bucket: str = None,
        prefix: str = None,
        source: str = None,
        run_id: str = None,
        client: storage.Client = None,
    ):
        """Initialize GCS writer.

        Args:
            bucket: GCS bucket name (default from config.GCS_OUTPUT_BUCKET)
            prefix: Path prefix in bucket (default from config.GCS_OUTPUT_PREFIX)
            source: Source name for partitioning (default: 'agentic')
            run_id: Run ID for this batch (default: random UUID)
            client: Google Cloud Storage client (default: create new)
        """
        self.bucket_name = bucket or config.GCS_OUTPUT_BUCKET
        if not self.bucket_name:
            raise ValueError(
                "No output bucket configured. Set GCS_OUTPUT_BUCKET in config "
                "or pass bucket parameter to WriteToGcsTool."
            )

        self.prefix = (prefix if prefix is not None else config.GCS_ARTIFACTS_PREFIX).strip("/")
        self.source = source or "agentic"
        self.run_id = run_id or uuid.uuid4().hex[:12]

        self._client = client or storage.Client(project=config.PROJECT_ID)
        self._bucket = self._client.bucket(self.bucket_name)

        # Thread-safety for sequence counter (supports multi-threaded writer)
        self._lock = threading.Lock()
        self._seq = 0

        # Audit metadata
        self.files_written = 0
        self.rows_written = 0
        self.bytes_written = 0
        self.uploaded_uris = []

    def _next_path(self, batch_id: int) -> str:
        """Generate next partition path with incremented sequence.

        Format: {prefix}/source={source}/dt={date}/run={run_id}/batch-{batch_id:06d}-part-{seq:05d}.jsonl

        Args:
            batch_id: Batch identifier

        Returns:
            GCS object path (relative to bucket)
        """
        with self._lock:
            self._seq += 1
            seq = self._seq

        day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        parts = [p for p in (self.prefix,) if p]
        parts += [
            f"source={self.source}",
            f"dt={day}",
            f"run={self.run_id}",
            f"batch-{batch_id:06d}-part-{seq:05d}.jsonl",
        ]

        return "/".join(parts)

    def write_batch(self, rows: List[Dict[str, Any]], batch_id: int) -> Optional[str]:
        """Write batch of extracted rows to GCS as NDJSON.

        Each row becomes one line of JSON. Retries overwrite the same key,
        so failed uploads followed by retry cannot duplicate rows.

        Args:
            rows: List of extracted row dicts
            batch_id: Batch identifier for partitioning

        Returns:
            gs:// URI of written file, or None if batch was empty
        """
        if not rows:
            return None

        path = self._next_path(batch_id)

        # Compact JSON encoding: separators=(",", ":") minimizes file size
        # (RAW_LLM_RESPONSE dominates the output)
        body = "".join(
            json.dumps(row, separators=(",", ":"), default=str) + "\n" for row in rows
        ).encode("utf-8")

        blob = self._bucket.blob(path)

        # Upload as NDJSON. Retry re-PUTs the same key, overwriting rather than
        # appending, so a failed-then-retried upload cannot duplicate rows.
        blob.upload_from_string(body, content_type="application/x-ndjson")

        uri = f"gs://{self.bucket_name}/{path}"
        self.files_written += 1
        self.rows_written += len(rows)
        self.bytes_written += len(body)
        self.uploaded_uris.append(uri)

        logger.info(
            f"Wrote {len(rows)} rows ({len(body) / 1e6:.1f} MB) to {uri}"
        )

        return uri

    def __call__(self, inputs: Dict[str, Any]) -> str:
        """Tool interface: write extraction results to GCS.

        Args:
            inputs: {
                "extracted_rows": [{"guid": "...", "PERSON_EMAIL": "...", ...}, ...],
                "batch_id": 1,
                "guid": "document-guid",
                "extracted_at": "2026-08-13T18:00:00Z"
            }

        Returns:
            JSON string with write status and URI
        """
        try:
            extracted_rows = inputs.get("extracted_rows", [])
            batch_id = inputs.get("batch_id", 1)
            guid = inputs.get("guid", "unknown")
            extracted_at = inputs.get("extracted_at", datetime.datetime.now(datetime.timezone.utc).isoformat())

            if not extracted_rows:
                return json.dumps({
                    "status": "success",
                    "guid": guid,
                    "rows_written": 0,
                    "uri": None,
                    "message": "No rows to write",
                })

            # Add audit metadata to each row
            rows_with_metadata = []
            for row in extracted_rows:
                row_copy = dict(row)
                row_copy["guid"] = guid
                row_copy["EXTRACTED_AT"] = extracted_at
                rows_with_metadata.append(row_copy)

            # Write to GCS
            uri = self.write_batch(rows_with_metadata, batch_id)

            return json.dumps({
                "status": "success",
                "guid": guid,
                "batch_id": batch_id,
                "rows_written": len(rows_with_metadata),
                "bytes_written": self.bytes_written,
                "uri": uri,
                "run_id": self.run_id,
            }, indent=2)

        except Exception as e:
            logger.error(f"Failed to write to GCS: {e}")
            return json.dumps({
                "status": "error",
                "error": str(e),
            })

    def summary(self) -> str:
        """Return summary of all writes in this session.

        Returns:
            Human-readable summary string
        """
        return (
            f"{self.files_written} file(s), {self.rows_written:,} rows, "
            f"{self.bytes_written / 1e6:.1f} MB to "
            f"gs://{self.bucket_name}/{self.prefix} (run={self.run_id})"
        )
