#!/usr/bin/env python3
"""One-time recovery of document_type for historical SKIPPED_DOCUMENT_TYPE
rejections, from before document_type was threaded through as a structured
field (see the commit that added it).

classify_document_type()'s result used to be folded only into a free-text
rejection_reason string ("Skipped: non-tabular document type (<type>)") and
then discarded. That string still survives in this machine's own
cache/sheet_ledger.db as each rejected document's failure_reason, so the
value can be recovered by parsing it back out - this script does exactly
that, for guids rejected on THIS machine only.

Why this can't just wait for the regular sync scripts to catch up:
sync_agentic_status.py's MERGE and sync_status_to_mosaic.py's UPDATE only
touch a guid when its *status* needs to change. A guid rejected before this
fix already has status='error_rejected' (ours) / 'agentic_error_rejected'
(mosaic's) from its original sync - that status isn't changing now, so the
regular sync will never revisit it to backfill document_type. This script
is the only path that reaches those already-synced rows.

sheet_ledger.db is local and per-machine (see its own module docstring) -
this only recovers what THIS machine rejected. Run the same script on the
other two drain machines to recover their own share.

Only ever sets document_type where it is currently NULL, in both tables -
never overwrites a value the forward-fix path (or a later reprocessing)
already wrote.

Usage:
    python scripts/backfill_document_type.py            # show what would change
    python scripts/backfill_document_type.py --apply     # apply it
"""
import argparse
import logging
import re
import sqlite3
import sys
from typing import Dict, List, Tuple

from google.cloud import bigquery

from extraction.core import config
from extraction.core.sheet_ledger import DEFAULT_LEDGER_DB

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

AGENTIC_TABLE_ID = f"{config.PROJECT_ID}.{config.DATASET_ID}.{config.SOURCE_TABLE_NAME}"
MOSAIC_TABLE_ID = "cio-mosaic-analytics-pr-853ae3.glean_extract.pii_extraction_status"

_DOC_TYPE_IN_REASON = re.compile(r"non-tabular document type \((.+)\)$")

# BigQuery caps a single UNNEST array parameter well above this, but keeping
# batches modest is what every other script here does (sync_status_to_mosaic.py,
# sync_agentic_status.py) - a smaller statement takes the table's DML lock for
# less time, and this is a one-time backfill, not something worth a bigger risk.
BATCH_SIZE = 5000


def recover_from_ledger(ledger_db: str) -> List[Tuple[str, str]]:
    """(guid, document_type) pairs parsed back out of failure_reason."""
    conn = sqlite3.connect(ledger_db)
    rows = conn.execute(
        "SELECT guid, failure_reason FROM sheet_details WHERE rejection_code = 'SKIPPED_DOCUMENT_TYPE'"
    ).fetchall()
    conn.close()

    recovered = []
    unparseable = 0
    for guid, failure_reason in rows:
        m = _DOC_TYPE_IN_REASON.search(failure_reason or "")
        if m:
            recovered.append((guid, m.group(1)))
        else:
            unparseable += 1
    if unparseable:
        logger.warning(f"{unparseable} SKIPPED_DOCUMENT_TYPE row(s) did not match the expected "
                        f"failure_reason shape and were skipped.")
    return recovered


def _batches(items: List, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def apply_to_agentic_table(client: bigquery.Client, pairs: List[Tuple[str, str]]) -> int:
    affected = 0
    for batch in _batches(pairs, BATCH_SIZE):
        job = client.query(
            f"""
            UPDATE `{AGENTIC_TABLE_ID}` a
            SET document_type = u.document_type
            FROM UNNEST(@pairs) AS u
            WHERE a.guid = u.guid AND a.document_type IS NULL
            """,
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ArrayQueryParameter("pairs", "RECORD", [
                    bigquery.StructQueryParameter(
                        None,
                        bigquery.ScalarQueryParameter("guid", "STRING", g),
                        bigquery.ScalarQueryParameter("document_type", "STRING", dt),
                    )
                    for g, dt in batch
                ]),
            ]),
        )
        job.result()
        affected += job.num_dml_affected_rows or 0
    return affected


def apply_to_mosaic_table(client: bigquery.Client, pairs: List[Tuple[str, str]]) -> int:
    affected = 0
    for batch in _batches(pairs, BATCH_SIZE):
        job = client.query(
            f"""
            UPDATE `{MOSAIC_TABLE_ID}` m
            SET document_type = u.document_type
            FROM UNNEST(@pairs) AS u
            WHERE m.guid = u.guid AND m.document_type IS NULL
            """,
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ArrayQueryParameter("pairs", "RECORD", [
                    bigquery.StructQueryParameter(
                        None,
                        bigquery.ScalarQueryParameter("guid", "STRING", g),
                        bigquery.ScalarQueryParameter("document_type", "STRING", dt),
                    )
                    for g, dt in batch
                ]),
            ]),
        )
        job.result()
        affected += job.num_dml_affected_rows or 0
    return affected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--ledger-db", default=DEFAULT_LEDGER_DB)
    args = parser.parse_args()

    if not config.PROJECT_ID:
        logger.error("No project. Set PROJECT_ID.")
        return 2

    pairs = recover_from_ledger(args.ledger_db)
    from collections import Counter
    counts = Counter(dt for _, dt in pairs)
    print(f"\nRecovered {len(pairs)} document_type value(s) from {args.ledger_db}:")
    for dt, n in counts.most_common():
        print(f"  {dt:40} {n:>6,}")
    print()

    if not args.apply:
        print("Dry run. Pass --apply to write these into both tables.\n")
        return 0
    if not pairs:
        logger.info("Nothing to recover.")
        return 0

    client = bigquery.Client(project=config.PROJECT_ID)

    n_ours = apply_to_agentic_table(client, pairs)
    logger.info(f"Backfilled document_type for {n_ours} row(s) in {AGENTIC_TABLE_ID}")

    n_mosaic = apply_to_mosaic_table(client, pairs)
    logger.info(f"Backfilled document_type for {n_mosaic} row(s) in {MOSAIC_TABLE_ID}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
