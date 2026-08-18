"""Durable per-sheet ledger: tabular status and a PII flag, one row per sheet.

For a guid that split into 5 sheets (extraction/core/records.py's
split_sheets()), this is 5 rows - each sheet's own outcome (did it pass, or
why not: not tabular, no data rows, or which pipeline step it failed at) and
its own PII signal, scored by population_selection.selector.classify_text()
against that sheet's own raw text rather than the whole document's.

Deliberately not part of extraction/core/workqueue.py: that SQLite file is a
transient bin-packing checkpoint for one backlog drain, and
WorkQueue.archive() renames the whole file aside once a drain finishes -
anything sharing that file would be scattered across however many archived
".done" files accumulate over time instead of staying one queryable table.
This lives in cache/, alongside schema_code_cache.db/column_labels.db, which
is durable runtime data by convention, not something that gets rotated away.

Written from run_pipeline.py (the one call site both the CLI and
run_corpus.py's _process_one go through), right after it builds
results["sheets"] - not from pipeline_agent.py, which stays focused on the
Look/Think/Test/Eval loop itself.
"""

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_LEDGER_DB = "cache/sheet_ledger.db"


class SheetLedger:
    """SQLite-backed record of every sheet a guid produced, and its PII flag."""

    def __init__(self, db_path: str = DEFAULT_LEDGER_DB):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # check_same_thread=False + a lock: run_corpus.py drains guids
        # concurrently (ThreadPoolExecutor), and each worker's run_pipeline()
        # call records its own guid's sheets independently.
        self._conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
        self._lock = threading.RLock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS sheet_details (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guid TEXT NOT NULL,
                sheet_index INTEGER NOT NULL,
                sheet_name TEXT,
                status TEXT NOT NULL,
                rejection_code TEXT,
                stage_failed TEXT,
                failure_reason TEXT,
                rows_extracted INTEGER NOT NULL DEFAULT 0,
                has_pii INTEGER NOT NULL,
                pii_score INTEGER NOT NULL,
                pii_signals TEXT,
                extraction_version TEXT,
                recorded_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sheet_details_guid ON sheet_details(guid);
            CREATE INDEX IF NOT EXISTS idx_sheet_details_pii ON sheet_details(has_pii);
            """)
            self._conn.commit()

    def record_sheets(
        self, guid: str, sheets: List[Dict[str, Any]],
        extraction_version: Optional[str] = None,
    ) -> None:
        """Replace this guid's rows with the sheets from its latest run.

        Reprocessing a guid deletes its old rows first, in the same
        transaction as inserting the fresh set - a re-run replaces its
        record rather than accumulating duplicates alongside it, the same
        principle the Parquet delivery path already uses.

        Args:
            guid: Document GUID.
            sheets: One dict per sheet, in document order - the same shape
                run_pipeline.py's _sheet_summary() returns (sheet_name,
                status, rejection_code, stage_failed, failure_reason,
                rows_extracted, has_pii, pii_score, pii_signals).
        """
        recorded_at = datetime.now(timezone.utc).isoformat()
        values = [
            (
                guid, index, s.get("sheet_name"), s.get("status"),
                s.get("rejection_code"), s.get("stage_failed"), s.get("failure_reason"),
                s.get("rows_extracted", 0), int(bool(s.get("has_pii"))),
                s.get("pii_score", 0), s.get("pii_signals") or "",
                extraction_version, recorded_at,
            )
            for index, s in enumerate(sheets)
        ]

        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN")
            try:
                cur.execute("DELETE FROM sheet_details WHERE guid = ?", (guid,))
                cur.executemany(
                    """INSERT INTO sheet_details (
                        guid, sheet_index, sheet_name, status, rejection_code,
                        stage_failed, failure_reason, rows_extracted, has_pii,
                        pii_score, pii_signals, extraction_version, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    values,
                )
                cur.execute("COMMIT")
            except BaseException:
                self._conn.rollback()
                logger.error(f"Failed to record sheet ledger for {guid}; rolled back")
                raise

    def sheets_for(self, guid: str) -> List[Dict[str, Any]]:
        """This guid's current sheet rows. For verification and tests."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT sheet_index, sheet_name, status, rejection_code, stage_failed, "
                "failure_reason, rows_extracted, has_pii, pii_score, pii_signals, "
                "extraction_version, recorded_at "
                "FROM sheet_details WHERE guid = ? ORDER BY sheet_index",
                (guid,),
            )
            columns = [c[0] for c in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


_ledger_instance: Optional[SheetLedger] = None


def get_ledger(db_path: str = DEFAULT_LEDGER_DB) -> SheetLedger:
    """Get or create the global ledger instance (singleton)."""
    global _ledger_instance
    if _ledger_instance is None:
        _ledger_instance = SheetLedger(db_path)
    return _ledger_instance
