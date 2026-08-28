#!/usr/bin/env python3
"""Flip already-processed guids out of 'pending' in the status table.

The pipeline writes its output and records every sheet's outcome in
cache/sheet_ledger.db, but nothing has been updating agentic_extraction_status.
The result is a status table claiming 1.9M documents are pending when a quarter
of a million of them have already been through, so a resumed run reprocesses
them.

The ledger is the source of truth here rather than a listing of the output
bucket: a guid can be legitimately processed and produce no file at all (no
tabular sheet, no data rows, no PII signal), and a bucket listing cannot tell
that apart from a guid that was never attempted.

Every guid in the ledger is marked complete. Sheet-level detail — which sheets
succeeded, which were rejected and why, which stage a failure hit — stays in the
ledger, so nothing is lost by not spreading it across status values.

Done as one staged load plus one UPDATE ... IN (SELECT ...). Chunked DML would
mean a dozen separate statements, each scanning a 4M-row table and each taking
its own table lock.

Usage:
    python scripts/reconcile_status.py                 # show what would change
    python scripts/reconcile_status.py --apply         # do it
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
STAGING_TABLE = "_reconcile_processed_guids"


def ledger_guids(path: str) -> list:
    """Every guid the pipeline has already handled."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [r[0] for r in conn.execute(
            "SELECT DISTINCT guid FROM sheet_details WHERE guid IS NOT NULL")]
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="Actually update; without it, only report")
    parser.add_argument("--ledger", default=LEDGER_DB)
    parser.add_argument("--source", default=config.SOURCE_TABLE)
    parser.add_argument("--version", default="agentic-v1-reconciled",
                        help="extraction_version stamped on reconciled rows")
    args = parser.parse_args()

    if not config.PROJECT_ID:
        logger.error("No project. Set PROJECT_ID.")
        return 2

    client = bigquery.Client(project=config.PROJECT_ID)
    status_table = f"{config.PROJECT_ID}.{config.DATASET_ID}.{config.SOURCE_TABLE_NAME}"
    staging_id = f"{config.PROJECT_ID}.{config.DATASET_ID}.{STAGING_TABLE}"

    guids = ledger_guids(args.ledger)
    logger.info(f"Ledger holds {len(guids)} processed guid(s)")
    if not guids:
        logger.info("Nothing to reconcile.")
        return 0

    # Stage the guid list so the update is one join rather than many IN lists.
    logger.info(f"Staging guids to {staging_id}")
    load = client.load_table_from_json(
        [{"guid": g} for g in guids],
        staging_id,
        job_config=bigquery.LoadJobConfig(
            schema=[bigquery.SchemaField("guid", "STRING", mode="REQUIRED")],
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        ),
    )
    load.result()

    counts = list(client.query(f"""
        SELECT s.status, COUNT(*) AS n
        FROM `{status_table}` s
        WHERE s.guid IN (SELECT guid FROM `{staging_id}`)
        GROUP BY s.status ORDER BY n DESC
    """).result())

    print(f"\nGuids in the ledger that appear in the status table:")
    total = 0
    for row in counts:
        print(f"  currently {row.status:20} {row.n:>8}")
        total += row.n
    print(f"  {'total':30} {total:>8}")
    missing = len(guids) - total
    if missing:
        print(f"  (not in the status table at all: {missing})")

    pending = sum(r.n for r in counts if r.status == "pending")
    print(f"\nWould flip {pending} row(s) from 'pending' to 'complete'.\n")

    if not args.apply:
        client.delete_table(staging_id, not_found_ok=True)
        print("Dry run. Pass --apply to make the change.\n")
        return 0

    job = client.query(
        f"""
        UPDATE `{status_table}`
        SET status = 'complete',
            extraction_version = @version,
            extracted_at = CURRENT_TIMESTAMP()
        WHERE status = 'pending'
          AND guid IN (SELECT guid FROM `{staging_id}`)
        """,
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("version", "STRING", args.version),
        ]),
    )
    job.result()
    updated = job.num_dml_affected_rows or 0
    logger.info(f"Marked {updated} guid(s) complete")

    client.delete_table(staging_id, not_found_ok=True)
    logger.info(f"Dropped {staging_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
