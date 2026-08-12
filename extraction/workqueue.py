"""Local, disk-persisted work queue for the queue-mode pipeline.

See config.py's QUEUE_MODE and pipeline.py's _run_batch_loop_queue for the
full picture. Summary: instead of repeatedly fetching pending batches from
BigQuery (`fetch_pending_batch`, ordered `body_length ASC`, one status UPDATE
per batch), queue mode does ONE metadata-only read of the whole pending
backlog (guid + body_length, no body_text -- bigquery_service.fetch_pending_metadata),
bin-packs it here into balanced bins, and drains those bins locally with no
further BigQuery writes until reconciliation (status_ledger.py + a
load_gcs_to_bq.py cron tick).

Why SQLite: bin assignments have to survive a crash (supervise_extraction.sh
restarts on any exit), and a source's backlog can be tens of millions of
guids -- too much to keep safely in a plain Python structure and re-derive
after a kill -9. A bin is only ever marked 'done' after its rows AND ledger
entries are durably in GCS (pipeline.py's writer thread does that), so the
'done' flag IS the crash-recovery checkpoint: a restart resumes at the next
'pending' bin instead of losing track of, or redoing, finished work.

Bin-packing: classic LPT (longest-processing-time-first). The number of bins
is `ceil(total_guids / QUEUE_TARGET_BIN_GUIDS)` -- a guid-count target, not a
byte target, because average body size varies enormously by source (measured
on real data 2026-08-06: emails averaged ~7.9 KB/guid; a byte budget tuned for
one source silently produces a very different guid count, and therefore a
very different memory/writer-queue/crash-recovery footprint, on another).
`QUEUE_TARGET_BIN_GUIDS` defaults to FETCH_BATCH_SIZE so a bin costs about
what the old fetch-batches cost along all three of those axes.

Within that fixed bin count, every guid -- largest body first -- goes into
whichever bin currently has the smallest total bytes, via a min-heap of just
`num_bins` entries (memory O(num_bins), regardless of backlog size). That is
what gives each bin a near-equal total body_length *within* its guid-count
budget, and, because big items land first and small ones backfill the gaps, a
deliberate mix of large and small bodies per bin (large items alone would
make one bin dominated by the slowest LLM calls).
"""
import heapq
import logging
import os
import sqlite3
import time
from typing import Iterable, List, Optional, Tuple

import bigquery_service
import config

logger = logging.getLogger(__name__)


def _db_path(source_table_name: str) -> str:
    os.makedirs(config.QUEUE_DB_DIR, exist_ok=True)
    return os.path.join(config.QUEUE_DB_DIR, f"{source_table_name}.sqlite")


class WorkQueue:
    """One source's balanced-bin backlog, persisted to a local SQLite file."""

    def __init__(self, path: str):
        self.path = path
        # autocommit (isolation_level=None); each write below opens its own
        # explicit transaction, so a bin's completion commit is a deliberate,
        # single durability boundary rather than an implicit one.
        self._conn = sqlite3.connect(path, isolation_level=None, timeout=30)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript("""
        CREATE TABLE IF NOT EXISTS items (
            guid TEXT PRIMARY KEY,
            body_length INTEGER NOT NULL,
            bin_id INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_items_bin ON items(bin_id);
        CREATE TABLE IF NOT EXISTS bins (
            bin_id INTEGER PRIMARY KEY,
            total_bytes INTEGER NOT NULL DEFAULT 0,
            num_guids INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending'
        );
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """)

    # -- introspection --------------------------------------------------- #

    def is_built(self) -> bool:
        return self._conn.execute("SELECT COUNT(*) FROM bins").fetchone()[0] > 0

    def is_fully_done(self) -> bool:
        remaining = self._conn.execute(
            "SELECT COUNT(*) FROM bins WHERE status != 'done'").fetchone()[0]
        return self.is_built() and remaining == 0

    def all_guids(self) -> List[str]:
        return [r[0] for r in self._conn.execute("SELECT guid FROM items").fetchall()]

    def total_bins(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM bins").fetchone()[0]

    def bin_status(self, bin_id: int) -> Optional[str]:
        row = self._conn.execute(
            "SELECT status FROM bins WHERE bin_id = ?", (bin_id,)).fetchone()
        return row[0] if row else None

    def progress(self) -> dict:
        total_bins, done_bins = self._conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(status = 'done'), 0) FROM bins").fetchone()
        total_guids, done_guids = self._conn.execute(
            "SELECT COALESCE(SUM(b.num_guids), 0), "
            "COALESCE(SUM(CASE WHEN b.status = 'done' THEN b.num_guids ELSE 0 END), 0) "
            "FROM bins b").fetchone()
        return {"total_bins": total_bins, "done_bins": done_bins,
                "total_guids": total_guids, "done_guids": done_guids}

    # -- build ------------------------------------------------------------ #

    def build(self, metadata_rows: Iterable[Tuple[str, int]], num_bins: int) -> None:
        """metadata_rows: iterable of (guid, body_length), largest body first.

        On any failure (e.g. the BigQuery stream backing metadata_rows drops
        mid-read), rolls back and re-raises rather than leaving an open
        transaction on this connection -- workqueue.build_or_resume retries a
        failed build as a whole unit on the SAME WorkQueue/connection, and a
        second `BEGIN` on top of an uncommitted one is a SQLite error, not a
        clean retry.
        """
        num_bins = max(1, num_bins)
        heap: List[Tuple[int, int]] = [(0, bin_id) for bin_id in range(num_bins)]
        heapq.heapify(heap)

        cur = self._conn.cursor()
        cur.execute("BEGIN")
        try:
            for bin_id in range(num_bins):
                cur.execute("INSERT INTO bins (bin_id, total_bytes, num_guids, status) "
                            "VALUES (?, 0, 0, 'pending')", (bin_id,))
            total_guids = 0
            totals_by_bin = {bin_id: 0 for bin_id in range(num_bins)}
            counts_by_bin = {bin_id: 0 for bin_id in range(num_bins)}
            for guid, body_length in metadata_rows:
                total_bytes, bin_id = heapq.heappop(heap)
                new_total = total_bytes + body_length
                heapq.heappush(heap, (new_total, bin_id))
                cur.execute("INSERT INTO items (guid, body_length, bin_id) VALUES (?, ?, ?)",
                            (guid, body_length, bin_id))
                totals_by_bin[bin_id] = new_total
                counts_by_bin[bin_id] += 1
                total_guids += 1
                # Flush per-bin aggregates periodically rather than per item --
                # a per-row UPDATE here would double the write volume of this
                # transaction for no benefit, since only the final totals matter.
                if total_guids % 50000 == 0:
                    cur.executemany(
                        "UPDATE bins SET total_bytes = ?, num_guids = ? WHERE bin_id = ?",
                        [(totals_by_bin[b], counts_by_bin[b], b) for b in totals_by_bin])
            cur.executemany(
                "UPDATE bins SET total_bytes = ?, num_guids = ? WHERE bin_id = ?",
                [(totals_by_bin[b], counts_by_bin[b], b) for b in totals_by_bin])
            cur.execute("COMMIT")
        except BaseException:
            self._conn.rollback()
            logger.warning(f"Work queue build failed partway through; rolled back "
                           f"({self.path}). Bins/items tables are unchanged, safe to retry.")
            raise
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('built_at', ?)", (str(time.time()),))
        logger.info(f"Work queue built: {total_guids} guid(s) across {num_bins} bin(s) "
                    f"-> {self.path}")

    # -- drain -------------------------------------------------------------- #

    def next_pending_bin(self) -> Optional[int]:
        row = self._conn.execute(
            "SELECT bin_id FROM bins WHERE status = 'pending' ORDER BY bin_id LIMIT 1"
        ).fetchone()
        return row[0] if row else None

    def bin_guids(self, bin_id: int) -> List[str]:
        return [r[0] for r in self._conn.execute(
            "SELECT guid FROM items WHERE bin_id = ?", (bin_id,)).fetchall()]

    def mark_bin_done(self, bin_id: int) -> None:
        """The crash-recovery checkpoint. Call only after this bin's rows and
        ledger entries are durably written to GCS -- see pipeline.py's writer.
        """
        self._conn.execute("UPDATE bins SET status = 'done' WHERE bin_id = ?", (bin_id,))

    def close(self) -> None:
        self._conn.close()

    def archive(self) -> str:
        """Renames this (fully-drained, reconciled) queue file aside so a
        fresh build starts clean without losing the record of what ran.
        """
        self.close()
        archived = f"{self.path}.{int(time.time())}.done"
        os.replace(self.path, archived)
        for ext in ("-wal", "-shm"):
            side = f"{self.path}{ext}"
            if os.path.exists(side):
                os.replace(side, f"{archived}{ext}")
        return archived


def build_or_resume(bq_client, status_table_id: str, source_table_name: str,
                    status_source: str, min_body_length: int,
                    target_bin_guids: int) -> Optional[WorkQueue]:
    """Returns a WorkQueue ready to drain, or None if there is genuinely
    nothing to do right now.

    Three cases:
      1. No queue file yet, or an empty/partial one from a build that never
         finished -- (re)build fresh from BigQuery's current pending backlog.
      2. An existing queue with undone bins -- resume it as-is; its 'pending'
         bins are exactly the work left over from before a crash/restart.
      3. An existing, fully-drained queue -- before reusing it for a new
         build, confirm load_gcs_to_bq.py's ledger reconciliation has already
         flipped these guids off 'pending' in BigQuery. Skipping this check
         would let a rebuild re-claim and reprocess the very guids this queue
         just finished, since queue mode never marks status itself.
    """
    path = _db_path(source_table_name)
    if os.path.exists(path):
        wq = WorkQueue(path)
        if wq.is_built() and not wq.is_fully_done():
            p = wq.progress()
            logger.info(f"Resuming work queue {path}: {p['done_bins']}/{p['total_bins']} "
                        f"bin(s) done ({p['done_guids']}/{p['total_guids']} guid(s)).")
            return wq
        if wq.is_built() and wq.is_fully_done():
            guids = wq.all_guids()
            still_pending = bigquery_service.retry_bq(
                "count_pending_guids (reconciliation check)",
                lambda: bigquery_service.count_pending_guids(
                    bq_client, status_table_id, status_source, guids))
            if still_pending:
                logger.warning(
                    f"Work queue {path} is fully drained but {still_pending} of its "
                    f"guid(s) are still 'pending' in BigQuery -- the status-ledger "
                    f"reconciliation (load_gcs_to_bq.py, run via cron) hasn't applied "
                    f"yet. NOT rebuilding: that would re-claim and reprocess work "
                    f"already done. Exiting quiescently; re-run once reconciled."
                )
                wq.close()
                return None
            logger.info(f"Work queue {path} fully drained and reconciled; archiving "
                        f"and building a fresh one.")
            wq.archive()
        else:
            logger.warning(f"Work queue {path} exists but was never fully built "
                           f"(interrupted mid-build); discarding and rebuilding.")
            wq.close()
            os.remove(path)
            for ext in ("-wal", "-shm"):
                side = f"{path}{ext}"
                if os.path.exists(side):
                    os.remove(side)

    total_guids, total_bytes = bigquery_service.retry_bq(
        "fetch_pending_totals",
        lambda: bigquery_service.fetch_pending_totals(
            bq_client, status_table_id, status_source, min_body_length))
    if total_guids == 0:
        logger.info(f"No pending '{status_source}' guid(s); nothing to queue.")
        return None

    # Bin count comes from the guid target, not a byte target -- see
    # config.QUEUE_TARGET_BIN_GUIDS for why (average body size varies wildly
    # by source, so a fixed byte budget doesn't generalize). LPT packing still
    # balances *bytes* within however many bins that produces.
    num_bins = max(1, -(-total_guids // max(1, target_bin_guids)))  # ceil div
    logger.info(f"Building work queue for {total_guids} pending '{status_source}' "
                f"guid(s), {total_bytes/1e6:.0f} MB total -> {num_bins} bin(s) "
                f"(target {target_bin_guids} guids/bin).")

    wq = WorkQueue(path)

    def _build_attempt():
        # A fresh generator each attempt: fetch_pending_metadata streams
        # results as the query runs, so a failure partway through can't
        # resume mid-stream -- it has to re-issue the query from scratch.
        # Safe to retry as a whole unit: wq.build() is one SQLite
        # transaction, so a failed attempt never leaves partial bins
        # committed for the next attempt to double up on.
        metadata = bigquery_service.fetch_pending_metadata(
            bq_client, status_table_id, status_source, min_body_length)
        wq.build(metadata, num_bins)

    bigquery_service.retry_bq(
        f"work queue build ({total_guids} guid metadata read + bin-pack)", _build_attempt)
    return wq
