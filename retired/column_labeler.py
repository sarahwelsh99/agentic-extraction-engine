"""LLM column labelling: decide which schema field a column holds.

The deterministic matcher recognises ~45 hardcoded English keywords, so it
misses 'Mail ID', 'Cell', 'Zip', 'SIN', 'Nombre' and every non-English header
in a corpus that spans several languages. Naming what a column holds is a
judgement about meaning, which is what a language model is for; matching
substrings is not.

Three properties make this affordable at corpus scale:

  * it is asked only about columns the rules could not resolve
  * every column of a document is asked in one request, not one each
  * answers are cached by column name, and column names repeat heavily across
    4M documents, so the same header is paid for once
"""

import hashlib
import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from extraction.core import config

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DB = "cache/column_labels.db"
NONE_LABEL = "NONE"

# Bumped whenever the prompt or the label set changes, so cached answers from
# an older question are not reused as if they answered the new one.
PROMPT_VERSION = "v3"

# Schema fields that describe a document rather than a person. Offering them as
# column labels made the classifier fire constantly: 'Yes'/'No' columns became
# BOOL_PERSONAL_DATA and 'Application ID' became RECORD_TYPE, neither of which
# is personal data about anybody.
NON_COLUMN_FIELDS = {
    "RECORD_TYPE", "JURISDICTION", "TELUS_BUSINESS", "COMPANY_NAME",
    "DOCUMENT_CLASSIFICATION", "OTHER_PII_TYPES",
}


def labelable_fields() -> List[str]:
    """Schema fields that can sensibly label a single column."""
    return [
        f for f in config.SCHEMA_FIELDS
        if f not in NON_COLUMN_FIELDS and not f.startswith("BOOL_")
    ]


class ColumnLabeler:
    """Label columns with mosaic schema fields using vLLM, with a cache."""

    MAX_NAME_CHARS = 80
    MAX_VALUE_CHARS = 32
    VALUES_PER_COLUMN = 2
    MAX_COLUMNS_PER_CALL = 40
    TOKENS_PER_COLUMN = 12

    def __init__(self, db_path: str = DEFAULT_CACHE_DB, timeout: int = None):
        self.db_path = db_path
        self.vllm_base = config.VLLM_API_BASE
        self.vllm_model = config.VLLM_MODEL
        self.timeout = timeout or config.VLLM_TIMEOUT
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS column_labels (
                   name_hash TEXT PRIMARY KEY,
                   column_name TEXT NOT NULL,
                   label TEXT NOT NULL,
                   created_at DATETIME DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        conn.commit()
        conn.close()

    @staticmethod
    def _key(column_name: str) -> str:
        """Cache key: case and punctuation folded, so near-identical headers share."""
        normalized = " ".join(re.findall(r"[a-z0-9]+", (column_name or "").lower()))
        # Prompt version is part of the key: a cached answer belongs to the
        # question that produced it.
        return hashlib.sha256(
            f"{PROMPT_VERSION}|{normalized}".encode()
        ).hexdigest()[:16]

    def _cached(self, names: List[str]) -> Dict[str, str]:
        if not names:
            return {}
        keys = {self._key(n): n for n in names}
        conn = sqlite3.connect(self.db_path)
        placeholders = ",".join("?" * len(keys))
        rows = conn.execute(
            f"SELECT name_hash, label FROM column_labels WHERE name_hash IN ({placeholders})",
            tuple(keys),
        ).fetchall()
        conn.close()
        return {keys[h]: label for h, label in rows if h in keys}

    def _store(self, labels: Dict[str, str]) -> None:
        if not labels:
            return
        conn = sqlite3.connect(self.db_path)
        conn.executemany(
            """INSERT OR REPLACE INTO column_labels (name_hash, column_name, label)
               VALUES (?, ?, ?)""",
            [(self._key(n), n[: self.MAX_NAME_CHARS], v) for n, v in labels.items()],
        )
        conn.commit()
        conn.close()

    def label(self, columns: List[Tuple[str, List[str]]]) -> Dict[str, Optional[str]]:
        """Label columns, consulting the cache before the model.

        Args:
            columns: (column_name, sample_values) pairs

        Returns:
            Mapping of column name to a schema field, or None for no PII.
            Columns the model could not be asked about are absent, so callers
            keep whatever the deterministic path decided.
        """
        names = [n for n, _ in columns if n and n.strip()]
        if not names:
            return {}

        resolved = self._cached(names)
        pending = [(n, v) for n, v in columns if n in set(names) and n not in resolved]

        fresh: Dict[str, str] = {}
        for start in range(0, len(pending), self.MAX_COLUMNS_PER_CALL):
            batch = pending[start : start + self.MAX_COLUMNS_PER_CALL]
            answers = self._ask(batch)
            if answers is None:
                # Model unavailable: leave these unresolved rather than guessing
                break
            fresh.update(answers)

        self._store(fresh)
        resolved.update(fresh)

        return {
            name: (None if label == NONE_LABEL else label)
            for name, label in resolved.items()
        }

    def _ask(self, batch: List[Tuple[str, List[str]]]) -> Optional[Dict[str, str]]:
        """One request for a batch of columns. None if the model was unreachable."""
        prompt = self._build_prompt(batch)
        try:
            response = requests.post(
                f"{self.vllm_base}/v1/completions",
                json={
                    "model": self.vllm_model,
                    "prompt": prompt,
                    "max_tokens": self.TOKENS_PER_COLUMN * len(batch) + 16,
                    "temperature": 0.0,
                    "stop": ["\n\n"],
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            text = response.json()["choices"][0].get("text", "")
        except Exception as e:
            logger.warning(f"Column labelling unavailable: {e}")
            return None

        return self._parse(text, batch)

    def _build_prompt(self, batch: List[Tuple[str, List[str]]]) -> str:
        allowed = ", ".join(labelable_fields())

        lines = []
        for i, (name, values) in enumerate(batch, 1):
            examples = [
                str(v)[: self.MAX_VALUE_CHARS]
                for v in (values or [])[: self.VALUES_PER_COLUMN]
            ]
            suffix = f"   examples: {examples}" if examples else ""
            lines.append(f"{i}. {name[: self.MAX_NAME_CHARS]!r}{suffix}")
        columns_str = "\n".join(lines)

        # Ends at the answer marker: this is a completions endpoint, so the
        # model continues from wherever the prompt stops.
        return f"""You classify spreadsheet columns by the kind of personal information they hold.

ALLOWED LABELS (choose exactly one per column, or {NONE_LABEL}):
{allowed}

RULES:
1. Answer {NONE_LABEL} unless the column clearly holds that personal data
   about an individual person. When unsure, answer {NONE_LABEL}.
2. An identifier counts only if it identifies a PERSON. Employee, staff, member,
   contributor and agent IDs are PERSON_ID; patient IDs are PATIENT_ID. But
   application, job post, project, ticket, case, incident and document IDs
   identify a record, not a person, and are {NONE_LABEL}.
3. A date is PERSON_DATE_OF_BIRTH only if it is someone's date of birth. Audit,
   submission, interaction, created and hire dates are {NONE_LABEL}.
4. A number is PERSON_PHONE_NUM only if it is a telephone number.
5. Company, product, team, site, template and file names are {NONE_LABEL}.
6. Yes/No, true/false, scores, ratings and free-text answers are {NONE_LABEL}.
7. Column names may be in any language, or abbreviated.

COLUMNS:
{columns_str}

Answer one line per column, formatted "<number>. <LABEL>", nothing else.

ANSWERS:
"""

    def _parse(
        self, text: str, batch: List[Tuple[str, List[str]]]
    ) -> Dict[str, str]:
        """Read back the numbered answers, keeping only valid labels."""
        allowed = set(config.SCHEMA_FIELDS) | {NONE_LABEL}

        answers: Dict[int, str] = {}
        for line in text.splitlines():
            match = re.match(r"\s*(\d+)\s*[.)]\s*([A-Z_]+)", line.strip())
            if not match:
                continue
            index, label = int(match.group(1)), match.group(2)
            # A label outside the schema is a hallucination, not a new field
            if label in allowed:
                answers[index] = label

        return {
            name: answers[i]
            for i, (name, _values) in enumerate(batch, 1)
            if i in answers
        }

    def stats(self) -> Dict[str, int]:
        conn = sqlite3.connect(self.db_path)
        total = conn.execute("SELECT COUNT(*) FROM column_labels").fetchone()[0]
        labelled = conn.execute(
            "SELECT COUNT(*) FROM column_labels WHERE label != ?", (NONE_LABEL,)
        ).fetchone()[0]
        conn.close()
        return {"columns_cached": total, "columns_with_pii": labelled}


_instance: Optional[ColumnLabeler] = None


def get_labeler(db_path: str = DEFAULT_CACHE_DB) -> ColumnLabeler:
    """Shared labeller instance."""
    global _instance
    if _instance is None:
        _instance = ColumnLabeler(db_path)
    return _instance
