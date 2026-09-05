#!/usr/bin/env python3
"""Share Tool 3's schema/code cache across the three machines draining the
mosaic backlog in parallel (see run_mosaic_structured.py).

Each machine keeps generating and reading its own local
cache/schema_code_cache.db - that part is unchanged and stays the hot path
(no network dependency per document). This script only carries new entries
between machines, on a 5-minute cron rather than every write, and through
BigQuery rather than a shared SQLite file: SQLite's WAL mode is not safe
against concurrent writers over NFS, which is the only filesystem these
machines actually share (10.24.24.4:/analytics_shared).

Why 5 minutes and not the 4-hour cadence sync_agentic_status.py uses: the
cache key (tools/generate_parser_script/tool.py's _cache_key - delimiter,
has_header_row, ragged) has so few possible values that a single machine
bootstraps its own complete local cache within its first ~10-15 minutes of
a drain. A slower sync would simply never arrive before every machine has
already generated every shape on its own, and would carry nothing useful.
Even at 5 minutes this only saves each machine's own bootstrap window
(observed: ~6 entries, a few minutes each) - it is not a sustained
optimization, since after bootstrap every machine is already at ~100% local
hits regardless of sharing.

Push is insert-only (WHEN NOT MATCHED THEN INSERT, never UPDATE): the first
machine to generate a working parser for a shape becomes that shape's
canonical entry, and later machines adopt it rather than overwrite it with
their own equally-valid-but-different generation. This matches the cache's
existing invariant (a hit means "a parser for this shape exists", verified
per-document by Tool 5 regardless of which machine produced it).

Pull is INSERT OR IGNORE against the local db's schema_hash PRIMARY KEY, so
an entry this machine already generated locally is never clobbered by a
remote copy of the same shape.

Usage:
    python scripts/sync_schema_cache.py            # show what would move
    python scripts/sync_schema_cache.py --apply    # actually sync

Cron (every 5 minutes, all three machines):
    */5 * * * * PROJECT_ID=cio-mosaic-analytics-pr-853ae3 \\
      PYTHONPATH=/home/jupyter/agentic-extraction-engine \\
      /opt/micromamba/bin/python3 \\
      /home/jupyter/agentic-extraction-engine/scripts/sync_schema_cache.py --apply \\
      >> /home/jupyter/agentic-extraction-engine/logs/sync_schema_cache.log 2>&1
"""

import argparse
import logging
import socket
import sqlite3
import sys
from typing import List, Tuple

from google.cloud import bigquery

from extraction.core import config
from extraction.schema_code_cache import DEFAULT_CACHE_DB, BUSY_TIMEOUT_MS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SHARED_TABLE_ID = f"{config.PROJECT_ID}.{config.DATASET_ID}.schema_code_cache_shared"

# (schema_hash, schema_json, code, code_length, created_at)
LocalRow = Tuple[str, str, str, int, str]


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def initialize_shared_table(client: bigquery.Client) -> None:
    schema = [
        bigquery.SchemaField("schema_hash", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("schema_json", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("code", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("code_length", "INTEGER", mode="NULLABLE"),
        bigquery.SchemaField("created_at", "TIMESTAMP", mode="NULLABLE"),
        bigquery.SchemaField("source_machine", "STRING", mode="NULLABLE"),
    ]
    table = bigquery.Table(SHARED_TABLE_ID, schema=schema)
    table.clustering_fields = ["schema_hash"]
    client.create_table(table, exists_ok=True)


def read_local(db_path: str) -> List[LocalRow]:
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT schema_hash, schema_json, code, code_length, created_at FROM code_cache"
    ).fetchall()
    conn.close()
    return rows


def push(client: bigquery.Client, rows: List[LocalRow], machine: str) -> int:
    """Insert-only: never overwrites a shape another machine already shared."""
    if not rows:
        return 0
    job = client.query(
        f"""
        MERGE `{SHARED_TABLE_ID}` T
        USING UNNEST(@rows) AS r
        ON T.schema_hash = r.schema_hash
        WHEN NOT MATCHED THEN
            INSERT (schema_hash, schema_json, code, code_length, created_at, source_machine)
            VALUES (r.schema_hash, r.schema_json, r.code, r.code_length, r.created_at, r.source_machine)
        """,
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ArrayQueryParameter("rows", "RECORD", [
                bigquery.StructQueryParameter(
                    None,
                    bigquery.ScalarQueryParameter("schema_hash", "STRING", h),
                    bigquery.ScalarQueryParameter("schema_json", "STRING", sj),
                    bigquery.ScalarQueryParameter("code", "STRING", c),
                    bigquery.ScalarQueryParameter("code_length", "INT64", cl),
                    bigquery.ScalarQueryParameter("created_at", "TIMESTAMP", ca),
                    bigquery.ScalarQueryParameter("source_machine", "STRING", machine),
                )
                for h, sj, c, cl, ca in rows
            ]),
        ]),
    )
    job.result()
    return job.num_dml_affected_rows or 0


def pull(client: bigquery.Client, db_path: str) -> int:
    """INSERT OR IGNORE against the local PRIMARY KEY - a shape this machine
    already generated itself is never replaced by a remote copy.
    """
    remote_rows = list(client.query(
        f"SELECT schema_hash, schema_json, code, code_length FROM `{SHARED_TABLE_ID}`"
    ).result())
    if not remote_rows:
        return 0

    conn = _connect(db_path)
    before = conn.total_changes
    conn.executemany(
        """INSERT OR IGNORE INTO code_cache
           (schema_hash, schema_json, code, code_length, created_at, hit_count)
           VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, 0)""",
        [(r.schema_hash, r.schema_json, r.code, r.code_length) for r in remote_rows],
    )
    conn.commit()
    added = conn.total_changes - before
    conn.close()
    return added


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--local-db", default=DEFAULT_CACHE_DB)
    args = parser.parse_args()

    if not config.PROJECT_ID:
        logger.error("No project. Set PROJECT_ID.")
        return 2

    machine = socket.gethostname()
    local_rows = read_local(args.local_db)
    print(f"\nLocal cache ({args.local_db}): {len(local_rows)} entrie(s).")

    if not args.apply:
        print("Dry run. Pass --apply to sync.\n")
        return 0

    client = bigquery.Client(project=config.PROJECT_ID)
    initialize_shared_table(client)

    pushed = push(client, local_rows, machine)
    logger.info(f"Pushed {pushed} new entrie(s) from {machine} to {SHARED_TABLE_ID}")

    pulled = pull(client, args.local_db)
    logger.info(f"Pulled {pulled} new entrie(s) from {SHARED_TABLE_ID} into {args.local_db}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
