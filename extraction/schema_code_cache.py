"""SQLite-based schema code cache for rapid code generation reuse.

Stores generated extraction code indexed by schema hash, enabling instant
retrieval when processing documents with identical column schemas.

Used by Tool 3 (generate_parser_script) to avoid expensive vLLM calls
for repeated schemas.
"""

import sqlite3
import json
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DB = "cache/schema_code_cache.db"


class SchemaCodeCache:
    """SQLite cache for generated extraction code indexed by schema hash.

    Thread-safe concurrent access for 96+ parallel workers.
    Automatically manages cache size and provides statistics.
    """

    def __init__(self, db_path: str = DEFAULT_CACHE_DB, max_schemas: int = 10000):
        """Initialize SQLite cache.

        Args:
            db_path: Path to SQLite database file
            max_schemas: Max schemas to keep (oldest least-used entries deleted)
        """
        self.db_path = db_path
        self.max_schemas = max_schemas

        # Create cache directory
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # Initialize database
        self._init_db()

    def _init_db(self) -> None:
        """Create database schema and indexes."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")  # Enable concurrent access

        # Main cache table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS code_cache (
                schema_hash TEXT PRIMARY KEY,
                schema_json TEXT NOT NULL,
                code TEXT NOT NULL,
                code_length INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_hit_at DATETIME,
                hit_count INTEGER DEFAULT 0
            )
        """)

        # Indexes for fast lookups and cleanup
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_hit_count ON code_cache(hit_count DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_created_at ON code_cache(created_at DESC)"
        )

        # Metadata table for cache statistics
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cache_stats (
                stat_key TEXT PRIMARY KEY,
                stat_value TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()
        conn.close()

        logger.info(f"Schema code cache initialized: {self.db_path}")

    def _compute_schema_hash(self, key: Any) -> str:
        """Compute a deterministic hash from whatever identifies the code.

        The key is hashed as given rather than interpreted, so callers decide
        what makes two documents share generated code. Order matters.

        Args:
            key: Any JSON-serialisable structure describing the schema

        Returns:
            SHA256 hash string (first 16 chars for brevity)
        """
        canonical = json.dumps(key, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def get(self, columns: list) -> Optional[str]:
        """Retrieve cached code for this schema if available.

        Args:
            columns: List of column dicts from Tool 2

        Returns:
            Generated Python code string, or None if not cached
        """
        schema_hash = self._compute_schema_hash(columns)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            """SELECT code, hit_count FROM code_cache
               WHERE schema_hash = ?""",
            (schema_hash,),
        )
        row = cursor.fetchone()
        conn.close()

        if row:
            code, hit_count = row
            logger.debug(f"Cache HIT: schema_hash={schema_hash} (hit #{hit_count + 1})")

            # Increment hit count asynchronously (don't block caller)
            self._increment_hit_async(schema_hash)

            return code
        else:
            logger.debug(f"Cache MISS: schema_hash={schema_hash}")
            return None

    def set(self, columns: list, code: str) -> str:
        """Store generated code in cache indexed by schema.

        Args:
            columns: List of column dicts from Tool 2
            code: Generated Python extraction code

        Returns:
            Schema hash for logging/debugging
        """
        schema_hash = self._compute_schema_hash(columns)

        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT OR REPLACE INTO code_cache
               (schema_hash, schema_json, code, code_length, created_at, hit_count)
               VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, 0)""",
            (
                schema_hash,
                json.dumps(columns),
                code,
                len(code),
            ),
        )
        conn.commit()
        conn.close()

        logger.debug(
            f"Cache SET: schema_hash={schema_hash} code_len={len(code)} chars"
        )
        return schema_hash

    def _increment_hit_async(self, schema_hash: str) -> None:
        """Increment hit count for schema (non-blocking).

        Args:
            schema_hash: Schema hash to increment
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                """UPDATE code_cache
                   SET hit_count = hit_count + 1, last_hit_at = CURRENT_TIMESTAMP
                   WHERE schema_hash = ?""",
                (schema_hash,),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Failed to update hit count: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics.

        Returns:
            Dict with: total_schemas, total_hits, avg_hits, max_hits, cache_size_mb
        """
        conn = sqlite3.connect(self.db_path)

        # Get cache stats
        stats = conn.execute(
            """SELECT
                COUNT(*) as total_schemas,
                SUM(hit_count) as total_hits,
                AVG(hit_count) as avg_hits,
                MAX(hit_count) as max_hits,
                SUM(code_length) as total_code_size
            FROM code_cache"""
        ).fetchone()

        conn.close()

        total_schemas = stats[0] or 0
        total_hits = stats[1] or 0
        avg_hits = stats[2] or 0
        max_hits = stats[3] or 0
        total_size = stats[4] or 0

        return {
            "total_schemas": total_schemas,
            "total_hits": total_hits,
            "avg_hits_per_schema": round(avg_hits, 2),
            "max_hits_per_schema": max_hits,
            "cache_size_mb": round(total_size / 1024 / 1024, 2),
            "file_size_mb": round(Path(self.db_path).stat().st_size / 1024 / 1024, 3),
        }

    def cleanup_if_needed(self) -> None:
        """Remove least-used entries if cache exceeds max_schemas.

        Removes bottom 10% of entries by (hit_count, created_at).
        """
        conn = sqlite3.connect(self.db_path)

        count = conn.execute("SELECT COUNT(*) FROM code_cache").fetchone()[0]

        if count > self.max_schemas:
            remove_count = max(1, int(self.max_schemas * 0.1))
            logger.info(
                f"Cache cleanup: {count} schemas > {self.max_schemas} limit. "
                f"Removing {remove_count} least-used entries..."
            )

            conn.execute(
                f"""DELETE FROM code_cache
                   WHERE schema_hash IN (
                       SELECT schema_hash FROM code_cache
                       ORDER BY hit_count ASC, created_at ASC
                       LIMIT {remove_count}
                   )"""
            )
            conn.commit()

        conn.close()

    def clear(self) -> None:
        """Clear entire cache (for testing/reset)."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM code_cache")
        conn.commit()
        conn.close()
        logger.info("Cache cleared")

    def print_stats(self) -> None:
        """Print cache statistics to console."""
        stats = self.get_stats()
        print("\n" + "=" * 70)
        print("SCHEMA CODE CACHE STATISTICS")
        print("=" * 70)
        print(f"  Total schemas cached:        {stats['total_schemas']}")
        print(f"  Total cache hits:            {stats['total_hits']}")
        print(f"  Avg hits per schema:         {stats['avg_hits_per_schema']}")
        print(f"  Hottest schema (max hits):   {stats['max_hits_per_schema']}")
        print(f"  Cache size (code):           {stats['cache_size_mb']} MB")
        print(f"  Database file size:          {stats['file_size_mb']} MB")

        if stats["total_hits"] > 0:
            savings = stats["total_hits"] * 5.3  # ~5.3s per vLLM call saved
            hours_saved = savings / 3600
            print(f"\n  💾 Estimated time saved:     {hours_saved:.1f} hours")
        print("=" * 70 + "\n")


# Global cache instance
_cache_instance: Optional[SchemaCodeCache] = None


def get_cache(db_path: str = DEFAULT_CACHE_DB) -> SchemaCodeCache:
    """Get or create global cache instance (singleton).

    Args:
        db_path: Path to SQLite database

    Returns:
        SchemaCodeCache instance
    """
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = SchemaCodeCache(db_path)
    return _cache_instance
