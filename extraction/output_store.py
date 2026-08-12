"""Where extraction output goes: newline-delimited JSON objects in GCS.

The pipeline no longer writes extracted rows to BigQuery. It writes NDJSON files
to a bucket, and loading those into `pii_extraction` is a separate step run when
you want it. That removes the whole class of problems the write path had:

  * Streaming inserts (`insert_rows_json`) left rows in a streaming buffer for up
    to ~90 minutes, during which BigQuery refuses any DML touching them. The
    dedup DELETE therefore failed with a 400 "would affect rows in the streaming
    buffer" for any guid reprocessed soon after its last extraction -- so stale
    rows survived and duplicated, and on 2026-08-05 it killed a run outright.
  * Every write took a DML lock on `pii_extraction`, on top of the
    `mark_status_*` UPDATEs that are already the pipeline's slowest stage.
  * Per-flush DML cost seconds to minutes, which backed up the writer queue and
    stalled result collection (measured: collection fell to 4.8/s while the GPU
    stayed saturated, then caught up 4,000 results in half a second).

An object upload is a single HTTPS PUT: no locks, no buffer, no job quota, and a
retry simply overwrites the same key, so it cannot duplicate.

De-duplication moves to load time, which is where it belongs. A guid extracted
more than once appears more than once across these files; the loader should MERGE
on `guid` keeping the newest `EXTRACTED_AT`, instead of the old
delete-then-append dance.

Layout:

    gs://<bucket>/<prefix>/source=<source>/dt=<YYYY-MM-DD>/run=<run_id>/
        batch-<batch_id>-part-<seq>.jsonl

`source=` and `dt=` are Hive-style so a load or external table can select a
subset by partition. `run=` keeps concurrent or restarted runs from colliding,
and makes it obvious which files a given run produced.
"""

import datetime
import json
import logging
import threading
import uuid
from typing import List, Optional

from google.cloud import storage

import config

logger = logging.getLogger(__name__)


class GcsJsonOutput:
    """Writes batches of extraction rows as NDJSON objects.

    Thread-safety: `write()` is called only from the single writer thread, but the
    sequence counter is guarded anyway so a future multi-writer design cannot
    silently produce colliding object names.
    """

    def __init__(self, bucket: str = None, prefix: str = None, source: str = None,
                 run_id: str = None, client=None):
        self.bucket_name = bucket or config.GCS_OUTPUT_BUCKET
        if not self.bucket_name:
            raise SystemExit(
                "No output bucket configured. Set GCS_OUTPUT_BUCKET (see "
                "extraction/config.py) to the bucket that should receive the "
                "extraction NDJSON."
            )
        self.prefix = (prefix if prefix is not None else config.GCS_OUTPUT_PREFIX).strip("/")
        self.source = source or config.SOURCE_TABLE_NAME
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self._client = client or storage.Client(project=config.PROJECT_ID)
        # client.bucket() constructs a reference without an API call. get_bucket()
        # would need storage.buckets.get, which the workbench service account is
        # not granted -- it can read and write OBJECTS but not bucket metadata.
        self._bucket = self._client.bucket(self.bucket_name)
        self._lock = threading.Lock()
        self._seq = 0
        self.files_written = 0
        self.rows_written = 0
        self.bytes_written = 0

    def _next_path(self, batch_id: int) -> str:
        with self._lock:
            self._seq += 1
            seq = self._seq
        day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        parts = [p for p in (self.prefix,) if p]
        parts += [f"source={self.source}", f"dt={day}", f"run={self.run_id}",
                  f"batch-{batch_id:06d}-part-{seq:05d}.jsonl"]
        return "/".join(parts)

    def write(self, rows: List[dict], batch_id: int) -> Optional[str]:
        """Upload `rows` as one NDJSON object. Returns its gs:// URI."""
        if not rows:
            return None
        path = self._next_path(batch_id)
        # Compact separators: these files are machine-read, and RAW_LLM_RESPONSE
        # already dominates the size.
        body = "".join(json.dumps(r, separators=(",", ":"), default=str) + "\n"
                       for r in rows).encode("utf-8")
        blob = self._bucket.blob(path)
        # A retry re-PUTs the same key, overwriting rather than appending, so a
        # failed-then-retried upload cannot duplicate rows.
        blob.upload_from_string(body, content_type="application/x-ndjson")
        uri = f"gs://{self.bucket_name}/{path}"
        self.files_written += 1
        self.rows_written += len(rows)
        self.bytes_written += len(body)
        logger.info(f"Wrote {len(rows)} rows ({len(body)/1e6:.1f} MB) to {uri}")
        return uri

    def summary(self) -> str:
        return (f"{self.files_written} file(s), {self.rows_written:,} rows, "
                f"{self.bytes_written/1e6:.1f} MB to "
                f"gs://{self.bucket_name}/{self.prefix} (run={self.run_id})")
