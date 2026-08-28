#!/usr/bin/env python3
"""Move guids with a failed sheet out of 'complete' and into a retryable status.

scripts/reconcile_status.py marks every processed guid complete, which is what
stops a resumed run reprocessing a quarter of a million documents. But it treats
a guid whose sheets failed the same as one that succeeded, and 'complete' is
terminal -- requeue_status() only reaches error_* rows. This carves the failures
back out so they can be retried later without un-reconciling everything else.

A guid qualifies if any of its sheets is status='failed' in the ledger. That
includes guids where other sheets succeeded and have output: re-running one
rewrites every sheet's file at a fixed path, so a retry overwrites rather than
duplicates.

Rejections are deliberately not included. A sheet rejected as NOT_TABULAR,
NO_DATA_ROWS or NO_PII_SIGNAL reached a correct verdict, and re-running it would
reach the same one.

Usage:
    python scripts/park_failed_guids.py                # show what would change
    python scripts/park_failed_guids.py --apply        # do it
"""

import argparse
import logging
import sqlite3
import sys

from google.cloud import bigquery

from extraction.core import config

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

LEDGER_DB = "cache/sheet_ledger.db"
STAGING_TABLE = "_park_failed_guids"
TARGET_STATUS = "error_extraction"


def failed_guids(path: str) -> list:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [r[0] for r in conn.execute(
            "SELECT DISTINCT guid FROM sheet_details WHERE status = 'failed'")]
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--ledger", default=LEDGER_DB)
    args = parser.parse_args()

    if not config.PROJECT_ID:
        logger.error("No project. Set PROJECT_ID.")
        return 2

    client = bigquery.Client(project=config.PROJECT_ID)
    status_table = f"{config.PROJECT_ID}.{config.DATASET_ID}.{config.SOURCE_TABLE_NAME}"
    staging_id = f"{config.PROJECT_ID}.{config.DATASET_ID}.{STAGING_TABLE}"

    guids = failed_guids(args.ledger)
    logger.info(f"Ledger holds {len(guids)} guid(s) with at least one failed sheet")
    if not guids:
        return 0

    client.load_table_from_json(
        [{"guid": g} for g in guids],
        staging_id,
        job_config=bigquery.LoadJobConfig(
            schema=[bigquery.SchemaField("guid", "STRING", mode="REQUIRED")],
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        ),
    ).result()

    print("\nTheir current status:")
    for row in client.query(f"""
        SELECT status, COUNT(*) AS n FROM `{status_table}`
        WHERE guid IN (SELECT guid FROM `{staging_id}`)
        GROUP BY status ORDER BY n DESC
    """).result():
        print(f"  {row.status:22} {row.n:>8}")

    if not args.apply:
        client.delete_table(staging_id, not_found_ok=True)
        print(f"\nWould move the 'complete' ones to '{TARGET_STATUS}'.")
        print("Dry run. Pass --apply to make the change.\n")
        return 0

    job = client.query(f"""
        UPDATE `{status_table}`
        SET status = '{TARGET_STATUS}',
            error_message = 'at least one sheet failed; see cache/sheet_ledger.db',
            extracted_at = CURRENT_TIMESTAMP()
        WHERE status = 'complete'
          AND guid IN (SELECT guid FROM `{staging_id}`)
    """)
    job.result()
    logger.info(f"Moved {job.num_dml_affected_rows or 0} guid(s) to {TARGET_STATUS}")
    client.delete_table(staging_id, not_found_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
