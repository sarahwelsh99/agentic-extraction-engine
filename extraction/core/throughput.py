"""Per-source, per-batch throughput recording.

pipeline.py already logged an ad-hoc rate mid-batch (every 500 guids, async
path only), but that line never survived past the log -- and email, drive and
chat each run as a separate `run.py --source` process (see
supervise_extraction.sh), so there was no single place to compare throughput
across sources. This module gives every batch loop one call to make at batch
end that:

  * logs a consistent line,
  * appends a row to throughput.csv (full per-batch history, easy to load with
    pandas/Excel), and
  * updates throughput.json (one running aggregate per source: total count,
    total time, overall rate, and the most recent batch).

Both files live at the repo root, not under extraction/, so they sit next to
the other run-level artifacts (logs/drive_run.log, logs/supervise.log, ...)
rather than inside the package.

Because sources run as separate OS processes that all write these same two
files, in-process locking (threading.Lock) is not enough -- record_batch()
takes an flock on a sidecar lock file so a concurrent writer from another
source's process still serializes instead of interleaving a partial CSV row or
clobbering another source's JSON entry with a stale read-modify-write.
"""

import csv
import fcntl
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from . import config

logger = logging.getLogger(__name__)

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_JSON_PATH = os.environ.get("THROUGHPUT_JSON_FILE", os.path.join(_ROOT_DIR, "throughput.json"))
DEFAULT_CSV_PATH = os.environ.get("THROUGHPUT_CSV_FILE", os.path.join(_ROOT_DIR, "throughput.csv"))
_LOCK_PATH = os.path.join(_ROOT_DIR, ".throughput.lock")

CSV_FIELDS = ["ts", "source", "batch_id", "count", "duration_sec", "rate_per_sec"]


class _CrossProcessLock:
    """flock on a sidecar file. Blocks other processes AND other threads --
    flock is scoped to the open file description, not the pid, so two fds
    opened by the same process still serialize against each other."""

    def __enter__(self):
        self._fh = open(_LOCK_PATH, "a")
        fcntl.flock(self._fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self._fh, fcntl.LOCK_UN)
        self._fh.close()


def _append_csv(path: str, entry: dict) -> None:
    is_new = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(entry)


def _update_json(path: str, entry: dict) -> None:
    try:
        with open(path, "r") as f:
            stats = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        stats = {}

    source_stats = stats.setdefault(entry["source"], {
        "batches": 0, "total_count": 0, "total_duration_sec": 0.0,
    })
    source_stats["batches"] += 1
    source_stats["total_count"] += entry["count"]
    source_stats["total_duration_sec"] += entry["duration_sec"]
    source_stats["overall_rate_per_sec"] = round(
        source_stats["total_count"] / max(source_stats["total_duration_sec"], 1e-6), 3)
    source_stats["last_batch"] = entry
    source_stats["updated_at"] = entry["ts"]

    # Write to a temp file and rename over the target: a crash or a concurrent
    # reader never observes a half-written JSON file this way.
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(stats, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def record_batch(batch_id: int, count: int, duration_sec: float,
                  source: Optional[str] = None,
                  json_path: Optional[str] = None,
                  csv_path: Optional[str] = None) -> dict:
    """Log and persist one batch's throughput. Returns the recorded entry.

    `duration_sec` should span exactly the work being measured (e.g.
    `time.monotonic() - started` around the LLM dispatch loop) -- callers
    decide what counts as "the batch" so this stays a plain recorder rather
    than a timer.
    """
    rate = count / max(duration_sec, 1e-6)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": source or config.SOURCE_TABLE_NAME,
        "batch_id": batch_id,
        "count": count,
        "duration_sec": round(duration_sec, 3),
        "rate_per_sec": round(rate, 3),
    }
    logger.info(f"Batch {batch_id}: {count} item(s) in {duration_sec:.0f}s ({rate:.1f}/s).")

    with _CrossProcessLock():
        _append_csv(csv_path or DEFAULT_CSV_PATH, entry)
        _update_json(json_path or DEFAULT_JSON_PATH, entry)
    return entry
