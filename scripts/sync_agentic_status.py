#!/usr/bin/env python3
"""Flush this machine's locally staged verdicts into agentic_extraction_status.

run_mosaic_structured.py used to MERGE into agentic_extraction_status directly,
once per bin per outcome. Moved off that path for the same reason mosaic's own
table was (see sync_status_to_mosaic.py's docstring): every MERGE takes the
table's DML lock, and several machines draining bins for hours were taking it a
dozen times an hour between them for no reason a few-times-a-day batch couldn't
serve just as well. Verdicts are staged locally per machine instead
(extraction/core/status_staging.py) and this carries them across.

Applied here even though agentic_extraction_status has no other known writer
today: the lock contention was between our own machines, not against an
outside table, so the same batching still helps.

Nothing depends on this running promptly. A guid whose verdict hasn't synced
yet is simply reprocessed on the next drain -- more work, not wrong work, since
the eventual MERGE overwrites in place either way.

Local and per-machine: each machine only ever flushes its own staging db, and
only ever clears the guids its own MERGE just committed.

Usage:
    python scripts/sync_agentic_status.py            # show what's staged
    python scripts/sync_agentic_status.py --apply    # flush it

Cron (every 4 hours):
    0 */4 * * * \
      PYTHONPATH=. python scripts/sync_agentic_status.py --apply \
      >> logs/sync_agentic_status.log 2>&1
"""

import argparse
import logging
import sys
from collections import Counter

from google.cloud import bigquery

from extraction.core import config, status_staging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

AGENTIC_TABLE_ID = f"{config.PROJECT_ID}.{config.DATASET_ID}.{config.SOURCE_TABLE_NAME}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--staging-db", default=status_staging.DEFAULT_STAGING_DB)
    parser.add_argument("--agentic-table", default=AGENTIC_TABLE_ID)
    args = parser.parse_args()

    if not config.PROJECT_ID:
        logger.error("No project. Set PROJECT_ID.")
        return 2

    verdicts = status_staging.drain(args.staging_db)

    counts = Counter(v[1] for v in verdicts)
    print("\nVerdicts staged here but not yet applied to agentic_extraction_status:")
    for status, n in counts.most_common():
        print(f"  {status:28} {n:>9,}")
    print(f"  {'total':28} {len(verdicts):>9,}\n")

    if not args.apply:
        print("Dry run. Pass --apply to sync.\n")
        return 0
    if not verdicts:
        logger.info("Nothing staged.")
        return 0

    client = bigquery.Client(project=config.PROJECT_ID)
    job = client.query(
        f"""
        MERGE `{args.agentic_table}` T
        USING UNNEST(@verdicts) AS v
        ON T.guid = v.guid
        WHEN MATCHED THEN UPDATE SET
            status = v.status,
            error_message = v.error_message,
            extraction_version = 'agentic-v1',
            gpu_machine = v.gpu_machine,
            source = v.source,
            extracted_at = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN
            INSERT (guid, status, error_message, extraction_version,
                    gpu_machine, source, extracted_at)
            VALUES (v.guid, v.status, v.error_message, 'agentic-v1',
                    v.gpu_machine, v.source, CURRENT_TIMESTAMP())
        """,
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ArrayQueryParameter("verdicts", "RECORD", [
                bigquery.StructQueryParameter(
                    None,
                    bigquery.ScalarQueryParameter("guid", "STRING", g),
                    bigquery.ScalarQueryParameter("status", "STRING", s),
                    bigquery.ScalarQueryParameter("error_message", "STRING", e),
                    bigquery.ScalarQueryParameter("gpu_machine", "STRING", m),
                    bigquery.ScalarQueryParameter("source", "STRING", src),
                )
                for g, s, e, m, src in verdicts
            ]),
        ]),
    )
    job.result()
    logger.info(f"Synced {job.num_dml_affected_rows or 0} row(s) to {args.agentic_table}")

    # Only clear what this MERGE actually just committed -- a verdict staged
    # after drain() read it (a guid reprocessed mid-sync) stays staged and is
    # picked up by the next run rather than lost.
    status_staging.clear([v[0] for v in verdicts], args.staging_db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
