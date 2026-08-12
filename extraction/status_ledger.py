"""Status-ledger NDJSON: how queue mode records each guid's outcome without
touching BigQuery mid-run.

Queue mode (config.QUEUE_MODE, pipeline.py's _run_batch_loop_queue) never calls
bigquery_service.mark_status_* while draining -- avoiding that per-batch UPDATE
is the whole point. But the terminal classification ('complete' / 'truncated' /
'error') can't be reliably reconstructed later from the GCS extraction rows
alone: load_gcs_to_bq.py's own drift-reconciliation code already documents this
exact ambiguity for the narrower case it handles (a parse-error 'complete' row
and a genuine 'truncated'/'error_llm' row can look identical in
RAW_LLM_RESPONSE/ERROR_MESSAGE). So the classification is written out
explicitly, once per guid, as its own small NDJSON stream, and applied in bulk
by load_gcs_to_bq.py on its existing cron cadence.

Layout mirrors output_store.GcsJsonOutput, in a separate prefix so the loader
can list/archive ledger objects without touching extraction-row objects:

    gs://<bucket>/<GCS_LEDGER_PREFIX>/source=<source>/dt=<YYYY-MM-DD>/run=<run_id>/
        ledger-<bin_id>-part-<seq>.jsonl
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


class GcsStatusLedger:
    """Writes bin-scoped ledger entries as NDJSON objects.

    Thread-safety mirrors GcsJsonOutput: written only from the single writer
    thread, sequence counter guarded regardless.
    """

    def __init__(self, bucket: str = None, prefix: str = None, source: str = None,
                run_id: str = None, client=None):
        self.bucket_name = bucket or config.GCS_OUTPUT_BUCKET
        self.prefix = (prefix if prefix is not None else config.GCS_LEDGER_PREFIX).strip("/")
        self.source = source or config.SOURCE_TABLE_NAME
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self._client = client or storage.Client(project=config.PROJECT_ID)
        self._bucket = self._client.bucket(self.bucket_name)
        self._lock = threading.Lock()
        self._seq = 0
        self.entries_written = 0

    def _next_path(self, bin_id: int) -> str:
        with self._lock:
            self._seq += 1
            seq = self._seq
        day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        parts = [p for p in (self.prefix,) if p]
        parts += [f"source={self.source}", f"dt={day}", f"run={self.run_id}",
                  f"ledger-{bin_id:06d}-part-{seq:05d}.jsonl"]
        return "/".join(parts)

    def write(self, entries: List[dict], bin_id: int) -> Optional[str]:
        """Upload `entries` ({guid, classification, prompt_version,
        extracted_at}) as one NDJSON object. Returns its gs:// URI.
        """
        if not entries:
            return None
        path = self._next_path(bin_id)
        body = "".join(json.dumps(e, separators=(",", ":")) + "\n" for e in entries).encode("utf-8")
        blob = self._bucket.blob(path)
        # A retry re-PUTs the same key -- overwrite, not append -- same
        # duplicate-proofing as GcsJsonOutput.write.
        blob.upload_from_string(body, content_type="application/x-ndjson")
        uri = f"gs://{self.bucket_name}/{path}"
        self.entries_written += len(entries)
        logger.info(f"Wrote {len(entries)} ledger entries to {uri}")
        return uri
