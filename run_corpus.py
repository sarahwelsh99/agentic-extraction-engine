#!/usr/bin/env python3
"""Drain the pending population through the extraction pipeline.

The fetch mechanism is mosaic-glean-extraction's queue mode, reading our own
population instead of theirs. Nothing here queries glean.drive_files: the
population lands in the status table once (population_selection/selector.py),
and every read after that is against the status table.

    phase 0   fetch_pending_totals            how many, how many bytes
    phase 1   fetch_pending_metadata          guid + body_length only, largest first
              WorkQueue.build                 LPT bin-pack into local SQLite
    phase 2   fetch_bodies_for_guids          bodies for one bin, just in time

Phase 1 carries no body_text, so the backlog can be sized and packed without
pulling the corpus through memory. Bodies arrive a bin at a time, one bin ahead
of the workers.

The local SQLite queue is the checkpoint and the only claim: there is no
BigQuery-side lock, so this assumes one machine drains one source at a time,
exactly as mosaic assumes it. Two concurrent runs against the same source would
duplicate work until the status marks caught up.

A bin's documents run concurrently, then the whole bin is written to GCS
(one Parquet file per document) and its statuses marked in one pair of
statements. Writing comes before marking on purpose: crash in between and the
guids stay pending, so they are reprocessed - each document's fixed,
guid-partitioned path means a reprocessed document overwrites its own file
rather than duplicating it (see write_parquet_to_gcs's docstring).

Usage:
    python run_corpus.py --dry-run            # size the backlog, build nothing
    python run_corpus.py --limit 50           # drain 50 documents
    python run_corpus.py                      # drain the backlog
    python run_corpus.py --requeue-errors     # retry previously parked failures first
"""

import argparse
import json
import logging
import os
import queue
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

from extraction.core import bigquery_service, config, workqueue
from run_pipeline import run_pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# Touch this file to stop cleanly at the next bin boundary rather than killing
# the process mid-bin and losing that bin's completed work.
STOP_FLAG_FILE = os.getenv("STOP_FLAG_FILE", "/tmp/agentic_extraction.stop")

# Status a document is parked in when it will not succeed on a retry as-is.
# Kept apart so a requeue can bring back the retryable ones and leave the rest.
STATUS_REJECTED = "rejected"      # Tool 2 refused it: no delimiter, single column
STATUS_EXTRACTION = "extraction"  # ran, but the evaluator failed it
STATUS_PIPELINE = "pipeline"      # raised

EXTRACTION_VERSION = os.getenv("EXTRACTION_VERSION", "agentic-v1")


def _stop_requested() -> bool:
    return os.path.exists(STOP_FLAG_FILE)


class _Prefetcher(threading.Thread):
    """Fetch the next bin's bodies while the current bin is being processed.

    Queue depth of one is deliberate: exactly one bin's bodies sit in memory
    ahead of the workers. A deeper queue would hold several bins of body_text at
    once for no gain, since the workers can only ever consume one bin at a time.
    """

    def __init__(self, client, status_table_id: str, wq: workqueue.WorkQueue,
                 source: Optional[str]):
        super().__init__(daemon=True)
        self._client = client
        self._status_table_id = status_table_id
        self._wq = wq
        self._source = source
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
                    lambda: bigquery_service.fetch_bodies_for_guids(
                        self._client, self._status_table_id, guids),
                )
                self.out.put((bin_id, rows))
        except BaseException as e:  # surfaced to the main loop, which stops
            self.error = e
            logger.error(f"Prefetch failed: {e}")
        finally:
            self.out.put(None)


def _sheet_detail(result: Dict) -> Optional[str]:
    """A specific per-sheet failure detail, when a document has more than one
    sheet and not every one of them passed - rather than one generic reason
    rolled up across all of them.
    """
    sheets = result.get("sheets") or []
    if len(sheets) <= 1:
        return None
    failing = [s for s in sheets if s.get("status") != "success"]
    if not failing:
        return None
    parts = [
        f"{s.get('sheet_name') or 'unnamed'} "
        f"({s.get('status')}{': ' + s.get('stage_failed') if s.get('stage_failed') else ''}"
        f"{': ' + s['failure_reason'] if s.get('failure_reason') else ''})"
        for s in failing[:3]
    ]
    passed = len(sheets) - len(failing)
    return f"{passed}/{len(sheets)} sheet(s) extracted; failed: " + ", ".join(parts)


def _process_one(guid: str, body_text: str) -> Dict:
    """Run one document through Tools 1-5 and report what happened.

    A multi-sheet document (extraction/core/records.py's split_sheets())
    counts as "complete" if at least one sheet passed - see run_pipeline.py's
    own docstring for the full partial-success semantics.

    Tool 6 is not called here: the caller loads the whole bin in one job.
    """
    try:
        result = run_pipeline(guid, body_text=body_text, load=False)
    except Exception as e:
        return {"guid": guid, "outcome": STATUS_PIPELINE, "detail": str(e), "rows": []}

    if result.get("rejected"):
        return {
            "guid": guid,
            "outcome": STATUS_REJECTED,
            "detail": (
                _sheet_detail(result)
                or result.get("rejection_reason") or result.get("rejection_code") or "rejected"
            ),
            "rows": [],
        }
    if not result.get("success"):
        return {
            "guid": guid,
            "outcome": STATUS_EXTRACTION,
            "detail": (
                _sheet_detail(result)
                or result.get("failure_reason") or result.get("error") or "did not pass"
            ),
            "rows": [],
        }

    detail = _sheet_detail(result)
    if detail:
        logger.info(f"{guid}: partial sheet success - {detail}")

    return {
        "guid": guid,
        "outcome": "complete",
        "detail": detail,
        "rows": result.get("extracted_rows") or [],
    }


def _run_bin(bin_id: int, rows: List[dict], workers: int) -> List[Dict]:
    """Process one bin's documents concurrently."""
    logger.info(f"Bin {bin_id}: {len(rows)} document(s) on {workers} worker(s)")
    results: List[Dict] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_process_one, r["guid"], r["body_text"]): r["guid"]
            for r in rows
        }
        for future in as_completed(futures):
            guid = futures[future]
            try:
                results.append(future.result())
            except Exception as e:
                # A worker should never escape _process_one, but a bin must not
                # be lost to one document if it does.
                logger.error(f"{guid}: worker raised: {e}")
                results.append({"guid": guid, "outcome": STATUS_PIPELINE,
                                "detail": str(e), "rows": []})
    return results


def _commit_bin(client, status_table_id: str, bin_id: int,
                results: List[Dict]) -> Tuple[int, int]:
    """Write a bin's rows (one Parquet file per document), then mark its statuses.

    Returns:
        (documents written, rows written)
    """
    from tools import get_tool_by_name

    passing = [r for r in results if r["outcome"] == "complete" and r["rows"]]
    empty = [r for r in results if r["outcome"] == "complete" and not r["rows"]]

    rows_loaded = 0
    if passing:
        writer = get_tool_by_name("write_parquet_to_gcs")
        response = json.loads(writer({
            "documents": [
                {"guid": r["guid"], "extracted_rows": r["rows"]} for r in passing
            ]
        }))
        if response.get("status") != "success":
            # Leave every guid pending: the bin is retried whole on the next run.
            raise RuntimeError(f"Bin {bin_id} write failed: {response.get('error')}")
        rows_loaded = response.get("rows_written", 0)
        logger.info(f"Bin {bin_id}: wrote {rows_loaded} row(s) from "
                    f"{len(passing)} document(s), one Parquet file each")

    # Marks come after the load. Complete first, so a crash between the two
    # groups leaves failures pending rather than successes.
    done = [r["guid"] for r in passing] + [r["guid"] for r in empty]
    if done:
        bigquery_service.retry_bq(
            f"mark_status_complete (bin {bin_id}, {len(done)} guid(s))",
            lambda: bigquery_service.mark_status_complete(
                client, status_table_id, done, EXTRACTION_VERSION),
        )

    for outcome in (STATUS_REJECTED, STATUS_EXTRACTION, STATUS_PIPELINE):
        failed = [r for r in results if r["outcome"] == outcome]
        if not failed:
            continue
        # One message for the group: per-document detail lives in the logs, and
        # a statement per document is what the batching exists to avoid.
        detail = failed[0]["detail"] or outcome
        bigquery_service.retry_bq(
            f"mark_status_error {outcome} (bin {bin_id}, {len(failed)} guid(s))",
            lambda o=outcome, f=failed, d=detail: bigquery_service.mark_status_error(
                client, status_table_id, [r["guid"] for r in f], o,
                f"{len(f)} document(s) in bin {bin_id}; first: {d}"),
        )
        logger.warning(f"Bin {bin_id}: parked {len(failed)} document(s) as error_{outcome}")

    # A bin that extracts nothing at all is never routine. One earlier run put a
    # full bin of 499 documents through in 23 minutes and produced zero rows,
    # because a method the sandbox needed had been deleted; every document
    # recorded an ordinary-looking extraction failure and nothing objected.
    # Rejections alone can legitimately empty a bin, so this reports loudly
    # rather than halting -- but it names where the cause is written down.
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
    parser.add_argument("--dry-run", action="store_true",
                        help="Report the backlog size and exit without building a queue")
    parser.add_argument("--limit", type=int,
                        help="Stop after roughly this many documents (rounded up to a bin)")
    parser.add_argument("--workers", type=int, default=config.MAX_WORKERS,
                        help=f"Documents in flight (default {config.MAX_WORKERS})")
    parser.add_argument("--bin-size", type=int, default=config.QUEUE_TARGET_BIN_GUIDS,
                        help=f"Documents per bin (default {config.QUEUE_TARGET_BIN_GUIDS})")
    parser.add_argument("--min-body-length", type=int, default=50)
    parser.add_argument("--source", default=config.SOURCE_TABLE,
                        help=f"Population to drain (default {config.SOURCE_TABLE})")
    parser.add_argument("--requeue-errors", action="store_true",
                        help="Return previously parked failures to pending before starting")
    parser.add_argument("--skip-init", action="store_true",
                        help="Do not touch the status table's schema")
    args = parser.parse_args()

    if not config.PROJECT_ID:
        logger.error("No project. Set PROJECT_ID.")
        return 2
    if _stop_requested():
        logger.error(f"Stop flag present at {STOP_FLAG_FILE}; remove it to run.")
        return 2

    client = bigquery_service.get_bigquery_client()
    status_table_id = bigquery_service.get_status_table_id(client)
    logger.info(f"Status table: {status_table_id}   population: {args.source}")

    if not args.skip_init:
        bigquery_service.initialize_status_table(client, status_table_id)

    if args.requeue_errors:
        for outcome in (STATUS_EXTRACTION, STATUS_PIPELINE):
            bigquery_service.requeue_status(
                client, status_table_id, f"error_{outcome}", source=args.source)
        # error_rejected is left alone: Tool 2 refused the document's structure,
        # and nothing about a rerun changes that.

    total_guids, total_bytes = bigquery_service.retry_bq(
        "fetch_pending_totals",
        lambda: bigquery_service.fetch_pending_totals(
            client, status_table_id, args.source, args.min_body_length),
    )
    logger.info(f"Pending backlog: {total_guids} document(s), {total_bytes/1e6:.0f} MB")

    if args.dry_run:
        bins = max(1, -(-total_guids // max(1, args.bin_size)))
        print(f"\nWould build {bins} bin(s) of up to {args.bin_size} document(s), "
              f"{args.workers} in flight.\nNothing was created.\n")
        return 0
    if total_guids == 0:
        logger.info("Nothing pending.")
        return 0

    wq = workqueue.build_or_resume(
        client, status_table_id,
        source_table_name=args.source,
        status_source=args.source,
        min_body_length=args.min_body_length,
        target_bin_guids=args.bin_size,
    )
    if wq is None:
        return 0

    prefetcher = _Prefetcher(client, status_table_id, wq, args.source)
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
            loaded_docs, loaded_rows = _commit_bin(
                client, status_table_id, bin_id, results)
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
            if args.limit and docs >= args.limit:
                logger.info(f"Reached --limit {args.limit}.")
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
