#!/usr/bin/env python3
"""Bring agentic_extraction_status into agreement with pii_extraction_status.

run_mosaic_structured.py drains mosaic's structured_pending and records each
bin's outcome in mosaic's table as agentic_complete / agentic_error_*. Our own
status table lists an overlapping population and, until the same run also marks
it, has no idea those documents are done -- which is what left 127k documents
sitting in 'pending' after a quarter of a million had been processed.

This copies mosaic's verdict across for every guid the agentic pipeline touched.
Both tables are in BigQuery, so it is one UPDATE with a join rather than the
ledger round-trip an earlier version of this used.

Whatever our selector had decided about a guid is overwritten, on purpose: the
tables are meant to agree about what was extracted. The selector's verdict
survives in pii_score / pii_signals / pii_detection_method, so "what did we
extract from the PII-relevant population" is still answerable.

Safe to re-run at any point, including while a run is in progress: it only ever
copies a verdict that mosaic's table already holds.

Usage:
    python scripts/align_status_tables.py             # show the drift
    python scripts/align_status_tables.py --apply     # close it
"""

import argparse
import logging
import sys

from google.cloud import bigquery

from extraction.core import config

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MOSAIC_TABLE = "cio-mosaic-analytics-pr-853ae3.glean_extract.pii_extraction_status"

# mosaic-side status -> our status. Only these are copied; anything mosaic's own
# pipeline wrote is none of our business.
VERDICTS = {
    "agentic_complete": "complete",
    "agentic_error_rejected": "error_rejected",
    "agentic_error_extraction": "error_extraction",
    "agentic_error_pipeline": "error_pipeline",
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
    verdict_cte = f"""
      verdicts AS (
        SELECT * FROM UNNEST([STRUCT<mosaic_status STRING, our_status STRING>
          {mapping}
        ])
      ),
      done AS (
        SELECT m.guid, v.our_status
        FROM `{args.mosaic_table}` m
        JOIN verdicts v ON v.mosaic_status = m.status
      )
    """

    print("\nDrift between the two tables:")
    rows = list(client.query(f"""
      WITH {verdict_cte}
      SELECT d.our_status AS should_be, o.status AS currently, COUNT(*) AS n
      FROM done d JOIN `{ours}` o USING (guid)
      GROUP BY should_be, currently ORDER BY n DESC
    """).result())

    drift = 0
    for r in rows:
        mark = "" if r.should_be == r.currently else "   <-- drift"
        if r.should_be != r.currently:
            drift += r.n
        print(f"  should be {r.should_be:18} currently {r.currently:18} {r.n:>9,}{mark}")
    print(f"\n  rows out of agreement: {drift:,}\n")

    if not args.apply:
        print("Dry run. Pass --apply to close it.\n")
        return 0
    if not drift:
        logger.info("Already aligned.")
        return 0

    job = client.query(f"""
      UPDATE `{ours}` o
      SET status = d.our_status,
          extraction_version = 'agentic-v1',
          extracted_at = CURRENT_TIMESTAMP()
      FROM (WITH {verdict_cte} SELECT * FROM done) d
      WHERE o.guid = d.guid AND o.status != d.our_status
    """)
    job.result()
    logger.info(f"Aligned {job.num_dml_affected_rows or 0} row(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
