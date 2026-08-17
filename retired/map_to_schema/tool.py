"""Tool 6: map extracted columns onto the mosaic schema.

Runs only on extractions the judge passed. The parser that produced them knows
nothing about meaning — it returns the document's own columns, named from its
header or numbered when it had none. This tool decides which of those columns
correspond to which schema fields, and rewrites the rows accordingly.

Deciding that a column called 'Mail ID', 'Courriel' or 'column_5' holds an email
address is a judgement about meaning, so the model makes it. The mapping is
cached by column name, and column names repeat heavily across the corpus, so a
header is paid for once rather than once per document.
"""

import json
import logging
from collections import Counter
from typing import Any, Dict, List, Optional

from extraction.column_labeler import get_labeler

logger = logging.getLogger(__name__)


class MapToSchemaTool:
    """Map a document's own columns to schema fields using the model."""

    name = "map_to_schema"
    description = "Map extracted columns onto the mosaic schema using the model"

    # Values shown to the model per column, to judge a column by its content
    # as well as its name. Positional columns have no useful name at all, so
    # the values are the only evidence.
    VALUES_PER_COLUMN = 3
    MAX_COLUMNS = 80

    def __call__(self, inputs: Dict[str, Any]) -> str:
        """Map one document's extracted rows onto the schema.

        Args:
            inputs: {
                "guid": "document-guid",
                "extracted_rows": [...],     # From Tool 4, judged acceptable
                "metadata_report": {...},    # From Tool 2, for the column order
            }

        Returns:
            JSON string with the mapped rows and the mapping that produced them
        """
        try:
            guid = inputs.get("guid", "unknown")
            rows = inputs.get("extracted_rows") or []
            report = inputs.get("metadata_report") or {}

            if not rows:
                return json.dumps({
                    "status": "success",
                    "guid": guid,
                    "mapped_rows": [],
                    "column_mapping": {},
                    "mapped_column_count": 0,
                    "message": "No rows to map",
                })

            columns = self._columns_with_values(rows, report)
            if not columns:
                return json.dumps({
                    "status": "success",
                    "guid": guid,
                    "mapped_rows": [],
                    "column_mapping": {},
                    "mapped_column_count": 0,
                    "message": "No columns to map",
                })

            labels = get_labeler().label(columns)
            mapping = {name: field for name, field in labels.items() if field}

            if not mapping:
                return json.dumps({
                    "status": "success",
                    "guid": guid,
                    "mapped_rows": [],
                    "column_mapping": {},
                    "mapped_column_count": 0,
                    "unmapped_column_count": len(columns),
                    "message": "No column corresponds to a schema field",
                })

            mapped_rows, duplicates = self._apply(rows, mapping)

            return json.dumps({
                "status": "success",
                "guid": guid,
                "mapped_rows": mapped_rows,
                "column_mapping": mapping,
                "mapped_column_count": len(mapping),
                "unmapped_column_count": len(columns) - len(mapping),
                # Two columns claiming one field is ambiguous, not an error: the
                # first is kept and the collision reported rather than hidden.
                "duplicate_target_fields": duplicates,
            }, indent=2)

        except Exception as e:
            logger.error(f"Schema mapping error: {e}")
            return json.dumps({"status": "error", "error": str(e)})

    def _columns_with_values(
        self, rows: List[Dict], report: Dict
    ) -> List[tuple]:
        """Collect each column with a few of its values, in document order.

        Args:
            rows: Extracted rows
            report: Metadata report, used for column order

        Returns:
            List of (column_name, sample_values)
        """
        ordered = [n for n in (report.get("header_names") or []) if n]
        if not ordered:
            # Fall back to whatever the rows carry, ignoring bookkeeping keys
            ordered = [k for k in rows[0] if not k.startswith("_")]

        columns = []
        for name in ordered[: self.MAX_COLUMNS]:
            values = []
            for row in rows:
                value = row.get(name)
                if value not in (None, ""):
                    values.append(str(value))
                if len(values) >= self.VALUES_PER_COLUMN:
                    break
            columns.append((name, values))
        return columns

    @staticmethod
    def _apply(
        rows: List[Dict], mapping: Dict[str, str]
    ) -> tuple[List[Dict], List[str]]:
        """Rewrite rows under their schema field names.

        Returns:
            Tuple of (mapped_rows, schema fields claimed by more than one column)
        """
        claimed = Counter(mapping.values())
        duplicates = sorted(f for f, n in claimed.items() if n > 1)

        mapped_rows = []
        for row in rows:
            mapped: Dict[str, Any] = {}
            for source, field in mapping.items():
                value = row.get(source)
                if value in (None, ""):
                    continue
                # First column to claim a field wins; the collision is reported
                mapped.setdefault(field, value)
            if not mapped:
                continue
            mapped["_row_number"] = row.get("_row_number")
            mapped["_valid"] = row.get("_valid", True)
            mapped_rows.append(mapped)

        return mapped_rows, duplicates
