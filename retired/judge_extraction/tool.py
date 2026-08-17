"""Tool 5: judge an execution and decide what happens to it.

Takes what Tool 4 got out of the document and decides whether it is good enough
to keep. Three outcomes:

  write      - the extraction stands; hand it to the GCS writer
  reprocess  - it failed and a retry is worth trying, with the reason attached
               so the next script generation knows what went wrong
  abandon    - it failed and retrying will not help, or there is nothing to keep

The judge performs no side effects. It does not write and it does not retry; it
returns the decision and the caller routes accordingly. That keeps the criteria
in one readable place and makes them testable without touching GCS.
"""

import json
import logging
from typing import Any, Dict, List, Optional

import requests

from extraction.core import config

logger = logging.getLogger(__name__)


class JudgeExtractionTool:
    """Judge an extraction with the model and route it."""

    name = "judge_extraction"
    description = "Judge an extraction result and decide: write, reprocess or abandon"

    # The model judges. These rates are the fallback for when it cannot be
    # reached, and a fast path for the unambiguous extremes when
    # SHORTCUT_EXTREMES is on — a perfect or a wholly failed extraction does
    # not need arbitration, and at corpus scale that is most documents.
    SUCCESS_RATE_AUTO_PASS = 0.95
    SUCCESS_RATE_AUTO_FAIL = 0.70
    SHORTCUT_EXTREMES = False
    LLM_JUDGE_TIMEOUT = 120

    # Attempts in total, including the first. Beyond this a failure is abandoned
    # rather than retried forever.
    MAX_ATTEMPTS = 2

    # Decisions
    WRITE = "write"
    REPROCESS = "reprocess"
    ABANDON = "abandon"

    def __init__(self):
        self.vllm_base = config.VLLM_API_BASE
        self.vllm_model = config.VLLM_MODEL

    def __call__(self, inputs: Dict[str, Any]) -> str:
        """Judge one execution.

        Args:
            inputs: {
                "guid": "document-guid",
                "execution_result": {...},   # From Tool 4
                "metadata_report": {...},    # From Tool 2
                "attempt": 1,                # 1 for the first run
            }

        Returns:
            JSON string with the verdict, the metrics behind it, and a decision
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

            # An execution that did not run at all is a failure of the script,
            # which is exactly what a retry might fix.
            if execution.get("status") != "success":
                return json.dumps({
                    "status": "success",
                    "guid": guid,
                    "verdict": "failure",
                    "method": "execution_failed",
                    "reasoning": f"Execution failed: {execution.get('error')}",
                    "decision": self._retry_or_abandon(attempt),
                    "attempt": attempt,
                    "feedback": self._feedback_from_error(execution.get("error")),
                    "quality_metrics": {},
                }, indent=2)

            rows = execution.get("extracted_rows", [])
            metrics = self._compute_metrics(rows, report)
            metrics["input_rows"] = execution.get("total_rows", 0)
            metrics["rows_skipped_wrong_shape"] = execution.get("skipped_wrong_shape", 0)

            verdict = self._judge(metrics, rows)
            decision, feedback = self._decide(verdict, metrics, attempt)

            return json.dumps({
                "status": "success",
                "guid": guid,
                "verdict": verdict["status"],
                "method": verdict["method"],
                "reasoning": verdict["reasoning"],
                "llm_judgment": verdict.get("llm_judgment"),
                "decision": decision,
                "attempt": attempt,
                "feedback": feedback,
                "quality_metrics": metrics,
            }, indent=2)

        except Exception as e:
            logger.error(f"Judging error: {e}")
            return json.dumps({"status": "error", "error": str(e)})

    def _decide(
        self, verdict: Dict[str, Any], metrics: Dict[str, Any], attempt: int
    ) -> tuple[str, Optional[str]]:
        """Turn a verdict into a routing decision.

        Returns:
            Tuple of (decision, feedback for the next attempt or None)
        """
        status = verdict["status"]

        if status in ("success", "partial"):
            return self.WRITE, None

        # Nothing came out, but nothing broke either. A retry would generate the
        # same script and read the same empty rows, so there is nothing to gain.
        if status in ("no_records", "no_data"):
            return self.ABANDON, None

        return self._retry_or_abandon(attempt), self._feedback_from_metrics(metrics)

    def _retry_or_abandon(self, attempt: int) -> str:
        return self.REPROCESS if attempt < self.MAX_ATTEMPTS else self.ABANDON

    @staticmethod
    def _feedback_from_error(error: Optional[str]) -> str:
        """Turn an execution error into an instruction for the next attempt."""
        text = str(error or "")[:300]
        return (
            "The previous script failed to run. Fix this and do not repeat it: "
            f"{text}"
        )

    @staticmethod
    def _feedback_from_metrics(metrics: Dict[str, Any]) -> str:
        """Turn failing metrics into an instruction for the next attempt.

        The specific errors matter more than the rate: they say what the script
        got wrong, which is what the next generation needs to avoid.
        """
        errors = [
            str(e.get("error"))[:120]
            for e in (metrics.get("validation_errors") or [])[:3]
        ]
        detail = "; ".join(errors) if errors else "no error detail was recorded"
        empty = metrics.get("always_empty_column_count") or 0

        note = (
            f"The previous script produced {metrics.get('records_out', 0)} rows of "
            f"which only {metrics.get('successful_rows', 0)} were valid. "
            f"Errors seen: {detail}."
        )
        if empty:
            note += (
                f" {empty} columns came back empty in every row, which suggests "
                f"fields were read from the wrong positions."
            )
        return note

    def _compute_metrics(
        self,
        extracted_rows: List[Dict],
        metadata_report: Dict = None,
    ) -> Dict[str, Any]:
        """Compute quality metrics from extraction results.

        Completeness is measured against the columns the document declares,
        which is the only thing available now that fields carry no meaning:
        a row is complete when it has a value for each header.

        Args:
            extracted_rows: Rows returned from extraction
            metadata_report: Structure from Tool 2

        Returns:
            Dict with quality metrics
        """
        report = metadata_report or {}
        total_rows = len(extracted_rows)
        successful_rows = sum(1 for r in extracted_rows if r.get("_valid", False))
        failed_rows = total_rows - successful_rows

        success_rate = successful_rows / total_rows if total_rows > 0 else 0.0

        validation_errors = []
        for row in extracted_rows:
            if not row.get("_valid"):
                for error in row.get("_errors", []):
                    validation_errors.append({
                        "row": row.get("_row_number"),
                        "error": error,
                    })

        # How full each row came back, over the document's own columns
        fields = [n for n in (report.get("header_names") or []) if n]
        completeness_per_row = []
        for row in extracted_rows:
            if not fields:
                break
            populated = sum(1 for f in fields if row.get(f) not in (None, ""))
            completeness_per_row.append(populated / len(fields))

        avg_completeness = (
            sum(completeness_per_row) / len(completeness_per_row)
            if completeness_per_row
            else 0.0
        )

        # Columns empty in every row: usually a sign the row split disagrees
        # with the header, worth surfacing rather than hiding.
        empty_columns = [
            f for f in fields
            if extracted_rows and all(r.get(f) in (None, "") for r in extracted_rows)
        ]

        return {
            # total_rows counts records returned, which is not the number of
            # rows parsed: a row can yield several records, or none.
            "total_rows": total_rows,
            "records_out": total_rows,
            "successful_rows": successful_rows,
            "failed_rows": failed_rows,
            "success_rate": success_rate,
            "failure_rate": 1.0 - success_rate,
            "average_field_completeness": avg_completeness,
            "expected_field_count": len(fields),
            "always_empty_columns": empty_columns[:10],
            "always_empty_column_count": len(empty_columns),
            "validation_error_count": len(validation_errors),
            "validation_errors": validation_errors[:10],
        }

    def _judge(self, metrics: Dict, extracted_rows: List[Dict]) -> Dict[str, Any]:
        """Score the extraction. The model decides; rates are the safety net.

        Two outcomes are settled before the model is asked, because they are
        facts about the run rather than judgements: no rows to parse, and rows
        parsed cleanly that yielded nothing. Both give a success rate of 0.0 and
        would otherwise read as catastrophic failures.

        Returns:
            Dict with status, method and reasoning
        """
        input_rows = metrics.get("input_rows", 0)
        records_out = metrics.get(
            "total_rows",
            metrics.get("successful_rows", 0) + metrics.get("failed_rows", 0),
        )

        if records_out == 0:
            if input_rows == 0:
                return {
                    "status": "no_data",
                    "method": "no_rows_to_parse",
                    "reasoning": "No data rows were available to parse",
                    "llm_judgment": None,
                }
            return {
                "status": "no_records",
                "method": "no_rows_produced",
                "reasoning": (
                    f"Parsed {input_rows} rows cleanly; every row came back "
                    f"empty, so the document yielded no records"
                ),
                "llm_judgment": None,
            }

        success_rate = metrics["success_rate"]

        # Optional fast path for the unambiguous extremes, off by default
        if self.SHORTCUT_EXTREMES:
            if success_rate >= self.SUCCESS_RATE_AUTO_PASS:
                return {
                    "status": "success",
                    "method": "fast_path_auto_pass",
                    "reasoning": (
                        f"Success rate {success_rate:.1%} exceeds auto-pass "
                        f"threshold {self.SUCCESS_RATE_AUTO_PASS:.0%}"
                    ),
                    "llm_judgment": None,
                }
            if success_rate <= self.SUCCESS_RATE_AUTO_FAIL:
                return {
                    "status": "failure",
                    "method": "fast_path_auto_fail",
                    "reasoning": (
                        f"Success rate {success_rate:.1%} below auto-fail "
                        f"threshold {self.SUCCESS_RATE_AUTO_FAIL:.0%}"
                    ),
                    "llm_judgment": None,
                }

        llm_judgment = self._get_llm_judgment(metrics, extracted_rows)

        # The model was unreachable, so fall back to the rates rather than
        # blocking the document
        if llm_judgment.get("_unavailable"):
            status = (
                "success" if success_rate >= self.SUCCESS_RATE_AUTO_PASS
                else "failure" if success_rate <= self.SUCCESS_RATE_AUTO_FAIL
                else "partial"
            )
            return {
                "status": status,
                "method": "threshold_fallback",
                "reasoning": (
                    f"Model unavailable; judged on success rate "
                    f"{success_rate:.1%} alone"
                ),
                "llm_judgment": None,
            }

        return {
            "status": llm_judgment.get("status", "partial"),
            "method": "llm_judgment",
            "reasoning": llm_judgment.get("reasoning", ""),
            "llm_judgment": llm_judgment,
        }

    def _get_llm_judgment(
        self, metrics: Dict, extracted_rows: List[Dict]
    ) -> Dict[str, Any]:
        """Ask the model whether a borderline extraction is acceptable."""
        prompt = f"""Evaluate the quality of a data extraction from a spreadsheet.

METRICS:
- Success Rate: {metrics.get('success_rate', 0):.1%}
- Rows returned: {metrics.get('records_out', 0)}
- Valid: {metrics.get('successful_rows', 0)}
- Invalid: {metrics.get('failed_rows', 0)}
- Average field completeness: {metrics.get('average_field_completeness', 0):.1%}
- Columns empty in every row: {metrics.get('always_empty_column_count', 0)} of {metrics.get('expected_field_count', 0)}

SAMPLE ROWS:
{json.dumps(extracted_rows[:2], indent=2, default=str)[:1200]}

ERRORS:
{json.dumps(metrics.get('validation_errors', [])[:5], indent=2, default=str)[:800]}

QUESTION: is this extraction acceptable to keep? Consider whether the failures
are a few bad rows in the source data, which is acceptable, or a systematic
misreading of the document, which is not.

Respond with JSON: {{"status": "success|partial|failure", "reasoning": "..."}}"""

        try:
            response = requests.post(
                f"{self.vllm_base}/v1/completions",
                json={
                    "model": self.vllm_model,
                    "prompt": prompt,
                    "max_tokens": 400,
                    "temperature": 0.3,
                },
                timeout=self.LLM_JUDGE_TIMEOUT,
            )
            result = response.json()
            if "choices" in result and result["choices"]:
                text = result["choices"][0].get("text", "").strip()
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {
                        "status": "partial",
                        "reasoning": f"Unparsed model judgment: {text[:200]}",
                    }
        except Exception as e:
            logger.warning(f"LLM judgment unavailable: {e}")

        # Signal unavailability rather than inventing a verdict; the caller
        # falls back to the rates and says so in the reasoning.
        return {"_unavailable": True}
