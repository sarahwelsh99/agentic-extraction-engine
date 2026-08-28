#!/usr/bin/env python3
"""Drain mosaic-glean-extraction's own backlog of documents it already
flagged as structured or too dense to extract - exactly the population this
agentic pipeline exists for.

Source: cio-mosaic-analytics-pr-853ae3.glean_extract.pii_extraction_status,
a separate, shared production table this repo does not own (different
schema than our own agentic_extraction_status: created_at/updated_at/md5/
prompt_version/qc_status, no pii_score/extraction_version/error_message).
We only ever read guid/body_text/body_length from it, filtered to
MOSAIC_SOURCE_STATUSES, and only ever write back to the exact guids one of
our bins just processed - every other row (including every other
structured_pending/error_dense row still queued in a later bin) is left
completely alone until its own bin commits. mosaic's own status vocabulary
is never reused for our outcomes: we write AGENTIC_* values instead, so a
downstream mosaic consumer can't mistake our result for one of its own.

Reuses run_corpus.py's per-document processing (_process_one/_run_bin) and
extraction/core/workqueue.py's LPT bin-packing verbatim - only the BigQuery
read/mark layer here is specific to this source table.

Usage:
    python run_mosaic_structured.py --dry-run
    python run_mosaic_structured.py --limit 500
    python run_mosaic_structured.py                # drain everything matching
"""
import argparse
import logging
import os
import sys
import threading
import queue
import time
from typing import Generator, List, Optional, Tuple

from google.cloud import bigquery

from extraction.core import bigquery_service, config, workqueue
from run_corpus import (
    _process_one, _run_bin, _stop_requested, STOP_FLAG_FILE,
    STATUS_REJECTED, STATUS_EXTRACTION, STATUS_PIPELINE, EXTRACTION_VERSION,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MOSAIC_PROJECT = "cio-mosaic-analytics-pr-853ae3"
MOSAIC_DATASET = "glean_extract"
MOSAIC_TABLE = "pii_extraction_status"
MOSAIC_TABLE_ID = f"{MOSAIC_PROJECT}.{MOSAIC_DATASET}.{MOSAIC_TABLE}"

# mosaic's own vocabulary for "we already know this document is structured
# and either deferred it or choked on it" - see docs/CLAUDE.md and this
# table's live status distribution.
#
# error_dense is deliberately excluded for now: a live run against it melted
# the local vLLM server (162k+ timeouts, zero bins committed) because
# extraction/core/records.py's SHEET_MARKER split - meant to detect real
# spreadsheet tabs - also fires on non-spreadsheet documents in this status
# (confirmed on guid 2d73b98d-f30c-5a4c-515a-6267df791dc9: a flattened PDF
# textbook with 3,373 spurious "sheets", not a workbook at all), fanning one
# document out into thousands of concurrent LLM calls with no cap.
# structured_pending has none of this: a 5,000-doc random sample showed zero
# SHEET_MARKER occurrences anywhere. Re-add error_dense only after
# split_sheets() has a sanity guard against pathological marker counts.
MOSAIC_SOURCE_STATUSES = ["structured_pending"]

# Our own outcomes, written back only to guids we actually processed.
# Prefixed so they can never collide with a status mosaic's own pipeline
# writes or reads.
AGENTIC_COMPLETE = "agentic_complete"


def _agentic_error_status(outcome: str) -> str:
    return f"agentic_error_{outcome}"


def _get_mosaic_client() -> bigquery.Client:
    return bigquery.Client(project=config.PROJECT_ID)


def fetch_totals(client: bigquery.Client, statuses: List[str],
                 min_body_length: int) -> Tuple[int, int]:
    query = f"""
    SELECT COUNT(*) AS n, COALESCE(SUM(body_length), 0) AS total_bytes
    FROM `{MOSAIC_TABLE_ID}`
    WHERE status IN UNNEST(@statuses)
      AND body_text IS NOT NULL
      AND body_length >= @min_len
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ArrayQueryParameter("statuses", "STRING", statuses),
        bigquery.ScalarQueryParameter("min_len", "INT64", min_body_length),
    ])
    row = list(client.query(query, job_config=job_config).result())[0]
    return int(row.n), int(row.total_bytes)


def fetch_metadata(client: bigquery.Client, statuses: List[str], min_body_length: int,
                   limit: Optional[int] = None) -> Generator[Tuple[str, int], None, None]:
    """Stream (guid, body_length), largest first - same shape LPT packing needs."""
    limit_sql = f"LIMIT {int(limit)}" if limit else ""
    query = f"""
    SELECT guid, body_length
    FROM `{MOSAIC_TABLE_ID}`
    WHERE status IN UNNEST(@statuses)
      AND body_text IS NOT NULL
      AND body_length >= @min_len
    ORDER BY body_length DESC
    {limit_sql}
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ArrayQueryParameter("statuses", "STRING", statuses),
        bigquery.ScalarQueryParameter("min_len", "INT64", min_body_length),
    ])
    query_job = client.query(query, job_config=job_config)
    for row in query_job.result(page_size=50000):
        yield row.guid, row.body_length


def fetch_bodies_for_guids(client: bigquery.Client, guids: List[str],
                           chunk_size: int = 15000) -> List[dict]:
    rows = []
    guids = list(guids)
    for i in range(0, len(guids), chunk_size):
        chunk = guids[i:i + chunk_size]
        job_config = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ArrayQueryParameter("guids", "STRING", chunk)])
        query = f"""
        SELECT guid, body_text, body_length
        FROM `{MOSAIC_TABLE_ID}`
        WHERE guid IN UNNEST(@guids)
        """
        rows.extend(client.query(query, job_config=job_config).result())
    return [{"guid": r.guid, "body_text": r.body_text, "body_length": r.body_length}
            for r in rows]


def mark_agentic_complete(client: bigquery.Client, guids: List[str]) -> int:
    if not guids:
        return 0
    job = client.query(
        f"""
        UPDATE `{MOSAIC_TABLE_ID}`
        SET status = @status, updated_at = CURRENT_TIMESTAMP()
        WHERE guid IN UNNEST(@guids)
        """,
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ArrayQueryParameter("guids", "STRING", guids),
            bigquery.ScalarQueryParameter("status", "STRING", AGENTIC_COMPLETE),
        ]),
    )
    job.result()
    return job.num_dml_affected_rows or 0


AGENTIC_TABLE_ID = f"{config.PROJECT_ID}.{config.DATASET_ID}.{config.SOURCE_TABLE_NAME}"


def mark_own_status(client: bigquery.Client, guids: List[str], status: str,
                    detail: Optional[str] = None) -> int:
    """Record the same outcome in our own status table, per bin.

    This run drains mosaic's structured_pending, so the marks above go to
    mosaic's table. Our status table tracks a different population that
    overlaps it, and without this it never learns that a guid it also lists
    has already been done -- which is what left 127k documents sitting in
    'pending' after a quarter of a million had been processed.

    Deliberately not scoped to rows still 'pending'. The two tables are meant to
    agree about what has been extracted, so a guid this run processed is marked
    here whatever our selector had previously decided about it -- including one
    it had ruled out as 'excluded_no_pii'. That verdict is not lost: pii_score,
    pii_signals and pii_detection_method still carry it, so "what did we extract
    from the PII-relevant population" stays answerable from those columns rather
    than from status.

    `detail` carries why a document failed. Without it this table records that
    198 documents failed and nothing about the cause, and answering "why" means
    going to the sheet ledger -- a SQLite file on whichever machine happened to
    run the drain. A whole bin once failed on a missing method, and the status
    table was unable to say so.
    """
    if not guids:
        return 0
    job = client.query(
        f"""
        UPDATE `{AGENTIC_TABLE_ID}`
        SET status = @status,
            error_message = @detail,
            extraction_version = 'agentic-v1',
            extracted_at = CURRENT_TIMESTAMP()
        WHERE guid IN UNNEST(@guids)
        """,
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ArrayQueryParameter("guids", "STRING", guids),
            bigquery.ScalarQueryParameter("status", "STRING", status),
            # Cleared on success, so a document that failed and later succeeded
            # does not keep its old reason.
            bigquery.ScalarQueryParameter("detail", "STRING",
                                          (detail or None) and detail[:8192]),
        ]),
    )
    job.result()
    return job.num_dml_affected_rows or 0


def mark_agentic_error(client: bigquery.Client, guids: List[str], outcome: str) -> int:
    if not guids:
        return 0
    job = client.query(
        f"""
        UPDATE `{MOSAIC_TABLE_ID}`
        SET status = @status, updated_at = CURRENT_TIMESTAMP()
        WHERE guid IN UNNEST(@guids)
        """,
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ArrayQueryParameter("guids", "STRING", guids),
            bigquery.ScalarQueryParameter("status", "STRING", _agentic_error_status(outcome)),
        ]),
    )
    job.result()
    return job.num_dml_affected_rows or 0


class _MosaicPrefetcher(threading.Thread):
    """Same one-bin-ahead prefetch as run_corpus.py's _Prefetcher, against
    this source's own fetch_bodies_for_guids instead of bigquery_service's.
    """

    def __init__(self, client: bigquery.Client, wq: workqueue.WorkQueue):
        super().__init__(daemon=True)
        self._client = client
        self._wq = wq
        self.out: "queue.Queue[Optional[Tuple[int, List[dict]]]]" = queue.Queue(maxsize=1)
        self.error: Optional[BaseException] = None

    def run(self) -> None:
        try:
            for bin_id in range(self._wq.total_bins()):
                if self._wq.bin_status(bin_id) == "done":
                    continue
                if _stop_requested():
                    break
                guids = self._wq.bin_guids(bin_id)
                if not guids:
                    continue
                rows = bigquery_service.retry_bq(
                    f"fetch_bodies_for_guids (bin {bin_id}, {len(guids)} guid(s))",
                    lambda: fetch_bodies_for_guids(self._client, guids),
                )
                self.out.put((bin_id, rows))
        except BaseException as e:
            self.error = e
            logger.error(f"Prefetch failed: {e}")
        finally:
            self.out.put(None)


def _build_or_resume(client: bigquery.Client, statuses: List[str], min_body_length: int,
                     target_bin_guids: int, limit: Optional[int]) -> Optional[workqueue.WorkQueue]:
    path = workqueue._db_path("mosaic_structured_dense")
    if os.path.exists(path):
        wq = workqueue.WorkQueue(path)
        if wq.is_built() and not wq.is_fully_done():
            p = wq.progress()
            logger.info(f"Resuming mosaic work queue: {p['done_bins']}/{p['total_bins']} "
                        f"bin(s) done ({p['done_guids']}/{p['total_guids']} guid(s)).")
            return wq
        if wq.is_built() and wq.is_fully_done():
            logger.info(f"Work queue {path} fully drained; archiving and building fresh.")
            wq.archive()
        else:
            logger.warning(f"Work queue {path} was never fully built; discarding.")
            wq.close()
            os.remove(path)
            for ext in ("-wal", "-shm"):
                side = f"{path}{ext}"
                if os.path.exists(side):
                    os.remove(side)

    total_guids, total_bytes = bigquery_service.retry_bq(
        "fetch_totals", lambda: fetch_totals(client, statuses, min_body_length))
    if limit:
        total_guids = min(total_guids, limit)
    if total_guids == 0:
        logger.info("No matching guid(s); nothing to queue.")
        return None

    num_bins = max(1, -(-total_guids // max(1, target_bin_guids)))
    logger.info(f"Building work queue for {total_guids} guid(s), {total_bytes/1e6:.0f} MB "
                f"(before limit) -> {num_bins} bin(s) (target {target_bin_guids} guids/bin).")

    wq = workqueue.WorkQueue(path)

    def _build_attempt():
        metadata = fetch_metadata(client, statuses, min_body_length, limit)
        wq.build(metadata, num_bins)

    bigquery_service.retry_bq(f"work queue build ({total_guids} guid metadata)", _build_attempt)
    return wq


def _commit_bin(client: bigquery.Client, bin_id: int, results: List[dict]) -> Tuple[int, int]:
    from tools import get_tool_by_name
    import json

    passing = [r for r in results if r["outcome"] == "complete" and r["rows"]]
    empty = [r for r in results if r["outcome"] == "complete" and not r["rows"]]

    rows_loaded = 0
    if passing:
        writer = get_tool_by_name("write_parquet_to_gcs")
        response = json.loads(writer({
            "documents": [{"guid": r["guid"], "extracted_rows": r["rows"]} for r in passing]
        }))
        if response.get("status") != "success":
            raise RuntimeError(f"Bin {bin_id} write failed: {response.get('error')}")
        rows_loaded = response.get("rows_written", 0)
        logger.info(f"Bin {bin_id}: wrote {rows_loaded} row(s) from "
                    f"{len(passing)} document(s), one Parquet file each")

    done = [r["guid"] for r in passing] + [r["guid"] for r in empty]
    if done:
        bigquery_service.retry_bq(
            f"mark_agentic_complete (bin {bin_id}, {len(done)} guid(s))",
            lambda: mark_agentic_complete(client, done),
        )
        ours = bigquery_service.retry_bq(
            f"mark_own_status complete (bin {bin_id}, {len(done)} guid(s))",
            lambda: mark_own_status(client, done, "complete"),
        )
        if ours:
            logger.info(f"Bin {bin_id}: {ours} of these were pending in "
                        f"{config.SOURCE_TABLE_NAME}; marked complete")

    for outcome in (STATUS_REJECTED, STATUS_EXTRACTION, STATUS_PIPELINE):
        failed = [r for r in results if r["outcome"] == outcome]
        if not failed:
            continue
        bigquery_service.retry_bq(
            f"mark_agentic_error {outcome} (bin {bin_id}, {len(failed)} guid(s))",
            lambda o=outcome, f=failed: mark_agentic_error(
                client, [r["guid"] for r in f], o),
        )
        bigquery_service.retry_bq(
            f"mark_own_status error_{outcome} (bin {bin_id}, {len(failed)} guid(s))",
            lambda o=outcome, f=failed: mark_own_status(
                client, [r["guid"] for r in f], f"error_{o}",
                # One reason for the group: they share a bin and an outcome, and
                # per-document detail is what the sheet ledger is for.
                f"{len(f)} document(s) in bin {bin_id}; first: "
                f"{f[0].get('detail') or o}"),
        )
        logger.warning(f"Bin {bin_id}: marked {len(failed)} document(s) as "
                       f"{_agentic_error_status(outcome)}")

    # A bin that extracts nothing at all is never routine. Bin 0 of an earlier
    # run put 499 documents through in 23 minutes and produced zero rows,
    # because a method the sandbox needed had been deleted; every document
    # recorded an ordinary-looking extraction failure and the run would have
    # continued producing nothing indefinitely. Rejections alone can legitimately
    # empty a bin, so this reports loudly rather than halting -- but it names the
    # place the cause is actually written down.
    if not passing and results:
        reasons = {r.get("detail") for r in results if r["outcome"] != "complete"}
        logger.error(
            f"Bin {bin_id}: {len(results)} document(s) produced ZERO rows. "
            f"That is not normal. Distinct failure reasons: "
            f"{sorted(str(x)[:120] for x in reasons if x) or 'none recorded'}. "
            f"Check cache/sheet_ledger.db failure_reason before letting this continue."
        )

    return len(passing) + len(empty), rows_loaded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, help="Cap the number of documents pulled in")
    parser.add_argument("--workers", type=int, default=config.MAX_WORKERS)
    parser.add_argument("--bin-size", type=int, default=config.QUEUE_TARGET_BIN_GUIDS)
    parser.add_argument("--min-body-length", type=int, default=50)
    parser.add_argument("--statuses", nargs="+", default=MOSAIC_SOURCE_STATUSES)
    args = parser.parse_args()

    if not config.PROJECT_ID:
        logger.error("No project. Set PROJECT_ID.")
        return 2
    if _stop_requested():
        logger.error(f"Stop flag present at {STOP_FLAG_FILE}; remove it to run.")
        return 2

    client = _get_mosaic_client()
    logger.info(f"Source: {MOSAIC_TABLE_ID}   statuses: {args.statuses}")

    total_guids, total_bytes = bigquery_service.retry_bq(
        "fetch_totals", lambda: fetch_totals(client, args.statuses, args.min_body_length))
    logger.info(f"Matching backlog: {total_guids} document(s), {total_bytes/1e6:.0f} MB")

    if args.dry_run:
        capped = min(total_guids, args.limit) if args.limit else total_guids
        bins = max(1, -(-capped // max(1, args.bin_size)))
        print(f"\nWould build {bins} bin(s) of up to {args.bin_size} document(s) "
              f"({capped} document(s) total after --limit), {args.workers} in flight.\n"
              f"Nothing was created.\n")
        return 0
    if total_guids == 0:
        logger.info("Nothing matching.")
        return 0

    wq = _build_or_resume(client, args.statuses, args.min_body_length, args.bin_size, args.limit)
    if wq is None:
        return 0

    prefetcher = _MosaicPrefetcher(client, wq)
    prefetcher.start()

    started = time.time()
    docs = rows_total = bins_done = 0
    try:
        while True:
            item = prefetcher.out.get()
            if item is None:
                break
            bin_id, bin_rows = item

            results = _run_bin(bin_id, bin_rows, args.workers)
            loaded_docs, loaded_rows = _commit_bin(client, bin_id, results)
            wq.mark_bin_done(bin_id)

            docs += len(results)
            rows_total += loaded_rows
            bins_done += 1
            p = wq.progress()
            logger.info(
                f"Bin {bin_id} done ({bins_done} bin(s), {docs} document(s), "
                f"{rows_total} row(s), {time.time()-started:.0f}s). "
                f"Queue: {p['done_bins']}/{p['total_bins']} bins."
            )

            if _stop_requested():
                logger.warning("Stop flag set; stopping at this bin boundary.")
                break
    finally:
        wq.close()

    if prefetcher.error:
        logger.error(f"Prefetch had failed: {prefetcher.error}")
        return 1

    elapsed = max(time.time() - started, 1e-6)
    print(f"\n{'='*72}")
    print(f"Documents: {docs}   rows loaded: {rows_total}   bins: {bins_done}")
    print(f"Elapsed:   {elapsed:.0f}s ({docs/elapsed*60:.1f} docs/min)")
    print(f"{'='*72}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
