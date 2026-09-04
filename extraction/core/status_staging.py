"""Local staging for per-guid verdicts destined for agentic_extraction_status.

run_mosaic_structured.py stages a verdict here per bin instead of writing
agentic_extraction_status directly, so a bin's runtime never waits on that
table. scripts/sync_agentic_status.py flushes this on a schedule (cron, every
4 hours) into one batched MERGE - the same lock-avoidance
scripts/sync_status_to_mosaic.py already uses for mosaic's own table, applied
here too on request even though agentic_extraction_status itself has no known
multi-writer contention today.

A guid whose verdict hasn't synced yet is invisible to fetch_totals/
fetch_metadata's anti-join and gets re-drained until it does - that costs
time, not correctness: the eventual MERGE overwrites in place either way.

Local and per-machine on purpose: each machine stages its own bins to its own
file and syncs independently, so nothing here needs cross-machine locking.
"""
import sqlite3
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

DEFAULT_STAGING_DB = "cache/agentic_status_staging.db"

# (guid, status, error_message, gpu_machine, source)
Verdict = Tuple[str, str, Optional[str], Optional[str], Optional[str]]


def _connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS staged_verdicts (
            guid TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            error_message TEXT,
            gpu_machine TEXT,
            source TEXT,
            staged_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def stage(verdicts: Iterable[Verdict], db_path: str = DEFAULT_STAGING_DB) -> None:
    """Record verdicts, INSERT OR REPLACE so a guid reprocessed before the
    last stage synced keeps only its latest outcome.
    """
    rows = list(verdicts)
    if not rows:
        return
    conn = _connect(db_path)
    conn.executemany(
        """INSERT OR REPLACE INTO staged_verdicts
           (guid, status, error_message, gpu_machine, source, staged_at)
           VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
        rows,
    )
    conn.commit()
    conn.close()


def drain(db_path: str = DEFAULT_STAGING_DB) -> List[Verdict]:
    """Every staged verdict, left in place - scripts/sync_agentic_status.py
    clears rows itself, only after its MERGE has actually committed.
    """
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT guid, status, error_message, gpu_machine, source FROM staged_verdicts"
    ).fetchall()
    conn.close()
    return rows


def clear(guids: List[str], db_path: str = DEFAULT_STAGING_DB) -> None:
    if not guids:
        return
    conn = _connect(db_path)
    conn.executemany("DELETE FROM staged_verdicts WHERE guid = ?", [(g,) for g in guids])
    conn.commit()
    conn.close()
