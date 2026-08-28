#!/usr/bin/env python3
"""Apply our extraction verdicts to mosaic's status table, on a schedule.

The drain records every bin's outcome in agentic_extraction_status, which has no
other writers, and does not touch mosaic's table at all. This carries those
verdicts across afterwards.

Why not per bin, as it used to be: four machines write to pii_extraction_status
and one of them updates millions of rows in a single statement. BigQuery
serializes DML per table, so a 200-row UPDATE from here can queue behind that for
minutes -- it was costing roughly nine minutes of every twenty-minute bin, and
before the retry budget was raised it killed a run outright. Batching a few
hours' verdicts into one statement per status takes the same lock a handful of
times a day instead of a dozen times an hour.

Nothing depends on this running promptly. The queue build anti-joins against our
own table, so work already done is skipped whether or not mosaic's table knows
about it yet. This is for other people reading progress from mosaic's side.

Safe to re-run at any time, including mid-bin: it only ever copies a verdict our
table already holds, and skips rows that already agree.

Usage:
    python scripts/sync_status_to_mosaic.py            # show the drift
    python scripts/sync_status_to_mosaic.py --apply    # apply it

Cron (every 4 hours):
    0 */4 * * * \
      PROJECT_ID=... PYTHONPATH=. python scripts/sync_status_to_mosaic.py --apply \
      >> logs/sync_status.log 2>&1
"""

import argparse
import logging
import sys

from google.cloud import bigquery

from extraction.core import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MOSAIC_TABLE = "cio-mosaic-analytics-pr-853ae3.glean_extract.pii_extraction_status"

# our status -> the status mosaic's table should carry. Prefixed on their side so
# our verdicts can never be confused with, or overwrite, mosaic's own pipeline.
VERDICTS = {
    "complete": "agentic_complete",
    "error_rejected": "agentic_error_rejected",
    "error_extraction": "agentic_error_extraction",
    "error_pipeline": "agentic_error_pipeline",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--mosaic-table", default=MOSAIC_TABLE)
    args = parser.parse_args()

    if not config.PROJECT_ID:
        logger.error("No project. Set PROJECT_ID.")
        return 2

    client = bigquery.Client(project=config.PROJECT_ID)
    ours = f"{config.PROJECT_ID}.{config.DATASET_ID}.{config.SOURCE_TABLE_NAME}"

    mapping = ", ".join(f"('{k}','{v}')" for k, v in VERDICTS.items())
    pending_cte = f"""
      verdicts AS (
        SELECT * FROM UNNEST([STRUCT<our_status STRING, mosaic_status STRING>
          {mapping}
        ])
      ),
      to_apply AS (
        SELECT a.guid, v.mosaic_status, a.gpu_machine
        FROM `{ours}` a
        JOIN verdicts v ON v.our_status = a.status
        JOIN `{args.mosaic_table}` m ON m.guid = a.guid
        WHERE m.status != v.mosaic_status
      )
    """

    rows = list(client.query(f"""
      WITH {pending_cte}
      SELECT mosaic_status, COUNT(*) AS n FROM to_apply
      GROUP BY 1 ORDER BY n DESC
    """).result())

    total = sum(r.n for r in rows)
    print("\nVerdicts held here but not yet reflected in mosaic's table:")
    for r in rows:
        print(f"  {r.mosaic_status:28} {r.n:>9,}")
    print(f"  {'total':28} {total:>9,}\n")

    if not args.apply:
        print("Dry run. Pass --apply to sync.\n")
        return 0
    if not total:
        logger.info("Already in sync.")
        return 0

    # One statement, not one per status: each takes the table lock, and that lock
    # is the entire reason this moved off the per-bin path.
    job = client.query(f"""
      UPDATE `{args.mosaic_table}` m
      SET status = t.mosaic_status,
          gpu_machine = COALESCE(t.gpu_machine, m.gpu_machine),
          updated_at = CURRENT_TIMESTAMP()
      FROM (WITH {pending_cte} SELECT * FROM to_apply) t
      WHERE m.guid = t.guid
    """)
    job.result()
    logger.info(f"Synced {job.num_dml_affected_rows or 0} row(s) to {args.mosaic_table}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
