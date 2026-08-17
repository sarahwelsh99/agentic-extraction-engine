"""Tool 5: decide whether an extraction worked.

Answers one question — did it work? — by comparing what Tool 4 got out against
the source document it came from. The answer is a boolean plus the numbers
behind it, so a reader can see why.

Three ways an extraction fails, all measured against the source rather than
against the extraction alone:

  the script would not run
  rows came back but too few of them parsed
  far fewer records came out than the source had rows

Nothing here interprets what the data means, and nothing here writes: a passing
extraction is handed to the loader by the caller.
"""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class EvaluateExtractionTool:
    """Judge whether an extraction succeeded, against the source document."""

    name = "evaluate_extraction"
    description = "Decide whether an extraction worked, comparing it to the source"

    # An extraction passes when nearly every row it returned parsed cleanly AND
    # it accounted for nearly every row of the source. Both are needed: a script
    # returning two perfect rows out of a thousand has not worked.
    MIN_VALID_ROW_SHARE = 0.90
    MIN_SOURCE_COVERAGE = 0.90

    # Share of the columns it was asked for that the script must actually return.
    # Short of this the values are landing under the wrong names: the caller zips
    # position to name, so a script that returns four values for a forty-column
    # table labels those four with the first four names, silently.
    MIN_COLUMN_DELIVERY = 0.90

    # Attempts in total, including the first, before a failure is given up on.
    MAX_ATTEMPTS = 2

    def __call__(self, inputs: Dict[str, Any]) -> str:
        """Evaluate one execution.

        Args:
            inputs: {
                "guid": "document-guid",
                "execution_result": {...},   # From Tool 4
                "metadata_report": {...},    # From Tool 2
                "attempt": 1,                # 1 for the first run
            }

        Returns:
            JSON string with extraction_passed, the evidence, and whether a
            retry is worth making
        """
        try:
            guid = inputs.get("guid", "unknown")
            execution = inputs.get("execution_result") or {}
            report = inputs.get("metadata_report") or {}
            attempt = int(inputs.get("attempt", 1))

            if not execution:
                return json.dumps({
                    "status": "error",
                    "error": "Missing execution_result in input",
                })

            # A script that would not run has plainly not worked, and that is
            # exactly the kind of failure a retry can fix.
            if execution.get("status") != "success":
                reason = f"Script failed to run: {execution.get('error')}"
                return json.dumps({
                    "status": "success",
                    "guid": guid,
                    "extraction_passed": False,
                    "failure_reason": reason,
                    "should_retry": attempt < self.MAX_ATTEMPTS,
                    "attempt": attempt,
                    "evaluation": {"executed": False},
                }, indent=2)

            rows = execution.get("extracted_rows", [])
            evaluation = self._evaluate(rows, execution, report)
            passed = evaluation["passed"]

            return json.dumps({
                "status": "success",
                "guid": guid,
                "extraction_passed": passed,
                "failure_reason": evaluation.get("failure_reason"),
                "should_retry": (not passed) and attempt < self.MAX_ATTEMPTS,
                "attempt": attempt,
                "evaluation": evaluation,
            }, indent=2)

        except Exception as e:
            logger.error(f"Evaluation error: {e}")
            return json.dumps({"status": "error", "error": str(e)})

    def _evaluate(
        self, rows: List[Dict], execution: Dict, report: Dict
    ) -> Dict[str, Any]:
        """Compare the extraction against the source document.

        Args:
            rows: Records the script returned
            execution: Execution counts from Tool 4
            report: Metadata report from Tool 2

        Returns:
            Dict with passed, the numbers behind it, and a reason when it failed
        """
        rows_in = execution.get("total_rows", 0)
        records_out = len(rows)
        valid = sum(1 for r in rows if r.get("_valid", False))

        valid_share = (valid / records_out) if records_out else 0.0
        coverage = (records_out / rows_in) if rows_in else 0.0

        # Widest row wins: a single short row should not be read as the script
        # having dropped columns everywhere.
        present = max(
            ([k for k in r if not k.startswith("_")] for r in rows[:20]),
            key=len, default=[],
        )
        expected_columns = report.get("modal_field_count") or 0

        evidence = {
            "executed": True,
            "source_rows_read": rows_in,
            "records_returned": records_out,
            "records_valid": valid,
            "valid_row_share": round(valid_share, 3),
            "source_coverage": round(coverage, 3),
            "columns_expected": expected_columns,
            "columns_present": len(present),
            "column_delivery": (
                round(len(present) / expected_columns, 3) if expected_columns else None
            ),
            "header_declares": report.get("header_field_count"),
            "rows_skipped_wrong_shape": execution.get("skipped_wrong_shape", 0),
            "sample_errors": [
                e for r in rows if not r.get("_valid")
                for e in (r.get("_errors") or [])
            ][:5],
        }

        if rows_in and records_out == 0:
            return {
                **evidence,
                "passed": False,
                "failure_reason": (
                    f"Read {rows_in} rows from the source and returned none"
                ),
            }

        if records_out == 0:
            # No rows in the source either, so there was nothing to extract.
            # Still not a pass: there is nothing to load.
            return {
                **evidence,
                "passed": False,
                "failure_reason": "The document held no data rows to extract",
            }

        if valid_share < self.MIN_VALID_ROW_SHARE:
            return {
                **evidence,
                "passed": False,
                "failure_reason": (
                    f"Only {valid}/{records_out} returned rows parsed cleanly "
                    f"({valid_share:.0%}, needs {self.MIN_VALID_ROW_SHARE:.0%}). "
                    f"Errors: {'; '.join(str(e)[:80] for e in evidence['sample_errors'][:2]) or 'none recorded'}"
                ),
            }

        # Fewer columns than asked for means the values are mislabelled, because
        # names are paired to positions by the caller. Retryable: the script was
        # told the right width and did not deliver it.
        if expected_columns and len(present) < expected_columns * self.MIN_COLUMN_DELIVERY:
            return {
                **evidence,
                "passed": False,
                "failure_reason": (
                    f"Returned {len(present)} columns where {expected_columns} were "
                    f"specified. Values are landing under the wrong column names. "
                    f"Return exactly {expected_columns} values per row, in column "
                    f"order, padding with None where a row is short"
                ),
            }

        if rows_in and coverage < self.MIN_SOURCE_COVERAGE:
            return {
                **evidence,
                "passed": False,
                "failure_reason": (
                    f"Returned {records_out} records from {rows_in} source rows "
                    f"({coverage:.0%}, needs {self.MIN_SOURCE_COVERAGE:.0%}); "
                    f"rows are being read from the wrong positions or dropped"
                ),
            }

        return {**evidence, "passed": True, "failure_reason": None}
