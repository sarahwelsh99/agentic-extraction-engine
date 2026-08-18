"""Pipeline metrics recording - similar to mosaic's throughput.py.

Records per-run metrics to:
  * metrics.csv - Append-only log of every pipeline run
  * metrics.json - Running aggregates per source/document type

Both files use cross-process locking to handle concurrent writes safely.

Example metrics.csv:
  ts,guid,source,batch_id,tool,duration_sec,rows_extracted,success
  2026-08-13T19:25:04Z,ddffbdb6-5041-4d65-a744-5a0631a629aa,agentic,1,tool_1,0.125,3,true
  2026-08-13T19:25:04Z,ddffbdb6-5041-4d65-a744-5a0631a629aa,agentic,1,tool_2,0.150,3,true

Example metrics.json:
  {
    "agentic": {
      "runs": 5,
      "total_rows_extracted": 127,
      "total_duration_sec": 45.2,
      "overall_rate_rows_per_sec": 2.8,
      "last_run": {...},
      "updated_at": "2026-08-13T19:25:11Z"
    }
  }
"""

import csv
import fcntl
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Root directory for metrics files (same level as run_pipeline.py)
_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_CSV_PATH = os.environ.get("METRICS_CSV_FILE", os.path.join(_ROOT_DIR, "metrics.csv"))
DEFAULT_JSON_PATH = os.environ.get("METRICS_JSON_FILE", os.path.join(_ROOT_DIR, "metrics.json"))
_LOCK_PATH = os.path.join(_ROOT_DIR, ".metrics.lock")

CSV_FIELDS = [
    "ts", "guid", "source", "batch_id",
    "tool1_duration", "tool1_elapsed",
    "tool2_duration", "tool2_elapsed",
    "tool3_duration", "tool3_elapsed",
    "tool4_duration", "tool4_elapsed",
    "tool5_duration", "tool5_elapsed",
    "tool6_duration", "tool6_elapsed",
    "total_duration", "total_rows_extracted", "success"
]


class _CrossProcessLock:
    """File lock using flock for cross-process synchronization.

    Blocks other processes AND threads. Ensures only one writer modifies
    metrics.csv and metrics.json at a time.
    """

    def __enter__(self):
        self._fh = open(_LOCK_PATH, "a")
        fcntl.flock(self._fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self._fh, fcntl.LOCK_UN)
        self._fh.close()


def _append_csv(path: str, entries: List[Dict]) -> None:
    """Append one or more rows to metrics.csv.

    Args:
        path: Path to CSV file
        entries: List of metric dictionaries
    """
    if not entries:
        return

    is_new = not os.path.exists(path) or os.path.getsize(path) == 0

    # The columns changed when the pipeline gained a stage. Appending new rows
    # under an old header silently misaligns every value, so the old file is
    # moved aside and a fresh one started with the current columns.
    if not is_new:
        with open(path, "r", newline="") as f:
            existing = (f.readline() or "").strip().split(",")
        if existing != CSV_FIELDS:
            rotated = f"{path}.{datetime.now(timezone.utc):%Y%m%d%H%M%S}.bak"
            os.rename(path, rotated)
            logger.warning(
                f"metrics columns changed; previous file kept at {rotated}"
            )
            is_new = True

    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if is_new:
            writer.writeheader()
        for entry in entries:
            writer.writerow(entry)


def _update_json(path: str, source: str, new_entries: List[Dict]) -> None:
    """Update metrics.json with new entries.

    Updates aggregate stats for a source and records the latest entry.
    Uses atomic writes (temp file + rename) to prevent corruption.

    Args:
        path: Path to JSON file
        source: Source name (e.g., "agentic")
        new_entries: List of metric entries
    """
    if not new_entries:
        return

    # Read existing stats
    try:
        with open(path, "r") as f:
            stats = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        stats = {}

    # Initialize source stats if needed
    source_stats = stats.setdefault(source, {
        "runs": 0,
        "total_rows_extracted": 0,
        "total_duration_sec": 0.0,
    })

    # Update aggregates
    source_stats["runs"] += 1
    for entry in new_entries:
        source_stats["total_rows_extracted"] += entry.get("rows_extracted", 0)
        source_stats["total_duration_sec"] += entry.get("duration_sec", 0)

    # Calculate overall rate
    total_duration = max(source_stats["total_duration_sec"], 1e-6)
    source_stats["overall_rate_rows_per_sec"] = round(
        source_stats["total_rows_extracted"] / total_duration, 3
    )

    # Record last entry
    source_stats["last_run"] = new_entries[-1]
    source_stats["updated_at"] = new_entries[-1]["ts"]

    # Atomic write: temp file -> rename
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(stats, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def record_pipeline_run(
    guid: str,
    batch_id: int,
    source: str,
    stage_metrics: Dict[str, Dict],
    total_rows_extracted: int,
    success: bool,
    csv_path: Optional[str] = None,
    json_path: Optional[str] = None,
) -> Dict:
    """Record a complete pipeline run's metrics.

    Args:
        guid: Document GUID
        batch_id: Batch identifier
        source: Source name (e.g., "agentic")
        stage_metrics: Dict of stage_name -> {duration_sec, rows_extracted, status}
        total_rows_extracted: Total rows extracted in this run
        success: Whether pipeline succeeded
        csv_path: Override default CSV path
        json_path: Override default JSON path

    Returns:
        Dict with recorded metrics
    """
    csv_path = csv_path or DEFAULT_CSV_PATH
    json_path = json_path or DEFAULT_JSON_PATH

    # Ensure directories exist
    Path(csv_path).parent.mkdir(parents=True, exist_ok=True)
    Path(json_path).parent.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).isoformat()

    # Build single entry per batch with all tools' metrics
    # Extract elapsed times from stage_metrics
    elapsed_times = {}
    for tool_name, metrics in stage_metrics.items():
        elapsed_times[tool_name] = metrics.get("elapsed_sec", metrics.get("duration_sec", 0))

    # Build entry with all tool metrics
    entry = {
        "ts": now,
        "guid": guid,
        "source": source,
        "batch_id": batch_id,
        "tool1_duration": round(stage_metrics.get("Tool 1: fetch_and_sample", {}).get("duration_sec", 0), 3),
        "tool1_elapsed": round(elapsed_times.get("Tool 1: fetch_and_sample", 0), 3),
        "tool2_duration": round(stage_metrics.get("Tool 2: structural_inspector", {}).get("duration_sec", 0), 3),
        "tool2_elapsed": round(elapsed_times.get("Tool 2: structural_inspector", 0), 3),
        "tool3_duration": round(stage_metrics.get("Tool 3: generate_parser_script", {}).get("duration_sec", 0), 3),
        "tool3_elapsed": round(elapsed_times.get("Tool 3: generate_parser_script", 0), 3),
        "tool4_duration": round(stage_metrics.get("Tool 4: sandbox_execute", {}).get("duration_sec", 0), 3),
        "tool4_elapsed": round(elapsed_times.get("Tool 4: sandbox_execute", 0), 3),
        "tool5_duration": round(stage_metrics.get("Tool 5: evaluate_extraction", {}).get("duration_sec", 0), 3),
        "tool5_elapsed": round(elapsed_times.get("Tool 5: evaluate_extraction", 0), 3),
        "tool6_duration": round(stage_metrics.get("Tool 6: write_parquet_to_gcs", {}).get("duration_sec", 0), 3),
        "tool6_elapsed": round(elapsed_times.get("Tool 6: write_parquet_to_gcs", 0), 3),
        "total_duration": round(sum(m.get("duration_sec", 0) for m in stage_metrics.values()), 3),
        "total_rows_extracted": total_rows_extracted,
        "success": "true" if success else "false",
    }

    csv_entries = [entry]
    total_duration = entry["total_duration"]

    # Log to console
    rate = total_rows_extracted / max(total_duration, 1e-6)
    logger.info(
        f"Pipeline complete: {total_rows_extracted} rows in {total_duration:.1f}s "
        f"({rate:.1f} rows/s). Success={success}"
    )

    # Write to files with lock
    with _CrossProcessLock():
        _append_csv(csv_path, csv_entries)

        # Build summary entry for JSON
        summary_entry = {
            "ts": now,
            "guid": guid,
            "batch_id": batch_id,
            "total_rows": total_rows_extracted,
            "total_duration_sec": round(total_duration, 3),
            "rate_rows_per_sec": round(rate, 3),
            "success": success,
        }
        _update_json(json_path, source, [summary_entry])

    return {
        "csv_path": csv_path,
        "json_path": json_path,
        "entries_written": len(csv_entries),
        "summary": summary_entry,
    }


def get_metrics_summary(json_path: Optional[str] = None, source: Optional[str] = None) -> Dict:
    """Get current metrics summary.

    Args:
        json_path: Path to metrics JSON file
        source: Optional source to filter (default: all)

    Returns:
        Dict with current metrics
    """
    json_path = json_path or DEFAULT_JSON_PATH

    if not os.path.exists(json_path):
        return {"message": "No metrics recorded yet"}

    with open(json_path, "r") as f:
        stats = json.load(f)

    if source:
        return stats.get(source, {})

    return stats


def get_csv_records(csv_path: Optional[str] = None, source: Optional[str] = None) -> List[Dict]:
    """Read all CSV records.

    Args:
        csv_path: Path to metrics CSV file
        source: Optional source to filter

    Returns:
        List of CSV rows as dicts
    """
    csv_path = csv_path or DEFAULT_CSV_PATH

    if not os.path.exists(csv_path):
        return []

    records = []
    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if source is None or row.get("source") == source:
                records.append(row)

    return records
