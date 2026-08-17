#!/usr/bin/env python3
"""Orchestration script for agentic extraction pipeline (Tools 1-5).

Chains Tools 1-5 together and logs all operations to a file for analysis and optimization.
Follows mosaic-glean-extraction's logging patterns for consistency.

Usage:
    python run_pipeline.py <guid>
    python run_pipeline.py ddffbdb6-5041-4d65-a744-5a0631a629aa
"""

import argparse
import datetime
import json
import logging
import os
import sys
import time
from pathlib import Path

# Configure logging FIRST before any imports that use logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Suppress verbose HTTP logging from requests/httpx
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

from tools import get_tool_by_name
from extraction.metrics_recorder import record_pipeline_run

# Attempts at generating a working script, including the first. Tool 5 decides
# whether a failure is worth retrying; this bounds how often it can say yes.
MAX_EXTRACTION_ATTEMPTS = 2


def _require_tool(name: str):
    """Fetch a tool, failing with the reason rather than a NoneType error.

    get_tool_by_name returns None when a tool cannot be constructed — usually
    missing configuration — which then surfaces as "'NoneType' object is not
    callable" several lines later.
    """
    tool = get_tool_by_name(name)
    if tool is None:
        raise RuntimeError(
            f"Tool '{name}' could not be created. Check its configuration "
            f"(see the error logged above)."
        )
    return tool


def _record_failure(guid: str, metrics: "PipelineMetrics") -> None:
    """Record an unsuccessful run without letting the recorder mask the error."""
    try:
        record_pipeline_run(
            guid=guid, batch_id=1, source="agentic",
            stage_metrics=metrics.stages,
            total_rows_extracted=0, success=False,
        )
    except Exception as e:
        logger.error(f"Failed to record failure metrics: {e}")


class PipelineMetrics:
    """Collect metrics throughout pipeline execution."""

    def __init__(self):
        self.start_time = datetime.datetime.now(datetime.timezone.utc)
        self.stages = {}
        self.total_rows_extracted = 0
        self.errors = []

    def record_stage(self, stage_name: str, start_time: float, end_time: float,
                     status: str, batch_start_time: float = None, **metadata):
        """Record metrics for a pipeline stage.

        Args:
            stage_name: Name of the stage (e.g., "Tool 1")
            start_time: Start time (time.time())
            end_time: End time (time.time())
            status: "success", "warning", or "error"
            batch_start_time: Start time of entire batch (for elapsed_sec)
            **metadata: Additional metrics to record
        """
        duration = end_time - start_time
        elapsed = (end_time - batch_start_time) if batch_start_time else duration
        self.stages[stage_name] = {
            "duration_sec": duration,
            "elapsed_sec": elapsed,
            "status": status,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            **metadata
        }

    def record_error(self, stage_name: str, error_msg: str):
        """Record an error that occurred."""
        self.errors.append({
            "stage": stage_name,
            "error": error_msg,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
        })

    def summary(self) -> dict:
        """Return complete metrics summary."""
        total_duration = (datetime.datetime.now(datetime.timezone.utc) - self.start_time).total_seconds()

        return {
            "start_time": self.start_time.isoformat(),
            "end_time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "total_duration_sec": total_duration,
            "stages": self.stages,
            "errors": self.errors,
            "total_rows_extracted": self.total_rows_extracted,
            "error_count": len(self.errors),
        }


def setup_logging(guid: str, log_dir: str = "logs") -> str:
    """Setup file logging for this pipeline run.

    Args:
        guid: Document GUID (used in log filename)
        log_dir: Directory to write logs to

    Returns:
        Path to log file
    """
    Path(log_dir).mkdir(exist_ok=True)

    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    guid_short = guid[:8] if guid else "unknown"
    log_file = os.path.join(log_dir, f"pipeline_{timestamp}_{guid_short}.log")

    # Add file handler to logger
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    file_handler.setFormatter(formatter)
    logging.getLogger().addHandler(file_handler)

    logger.info(f"Pipeline log file: {log_file}")
    return log_file


def run_pipeline(guid: str, body_text: str = None, load: bool = True) -> dict:
    """Run the complete extraction pipeline (Tools 1-5).

    Args:
        guid: Document GUID to extract
        body_text: Optional direct body text (for testing)
        load: Load a passing extraction with Tool 6. Set False to have the rows
            returned under "extracted_rows" instead, so a caller can load a
            whole batch of documents in one job.

    Returns:
        Dict with pipeline results and metrics
    """
    batch_start_time = time.time()
    metrics = PipelineMetrics()
    results = {
        "guid": guid,
        "success": False,
        "stages": {}
    }

    logger.info(f"Starting pipeline for guid: {guid}")
    logger.info("=" * 80)

    # ==================== Tool 1: fetch_and_sample ====================
    try:
        logger.info("TOOL 1: Fetching and sampling document...")
        tool1_start = time.time()

        tool1 = _require_tool("fetch_and_sample")
        tool1_response = json.loads(tool1({
            "guid": guid,
            "body_text": body_text,
            "sample_size": 5,
        }))

        tool1_end = time.time()

        if tool1_response.get("status") != "success":
            raise Exception(f"Tool 1 failed: {tool1_response.get('error')}")

        metrics.record_stage(
            "Tool 1: fetch_and_sample",
            tool1_start,
            tool1_end,
            "success",
            batch_start_time=batch_start_time,
            format=tool1_response.get("detected_format_hint"),
            total_bytes=tool1_response.get("total_bytes"),
            sample_size=tool1_response.get("sample_size")
        )

        results["stages"]["tool_1"] = tool1_response
        logger.info(f"✓ Tool 1 success: Format={tool1_response.get('detected_format_hint')}, "
                   f"Bytes={tool1_response.get('total_bytes')}, Sample={tool1_response.get('sample_size')} rows")

    except Exception as e:
        logger.error(f"✗ Tool 1 failed: {str(e)}")
        metrics.record_error("Tool 1", str(e))

        # Record failure metrics
        try:
            record_pipeline_run(
                guid=guid, batch_id=1, source="agentic",
                stage_metrics=metrics.stages,
                total_rows_extracted=0, success=False
            )
        except Exception as record_e:
            logger.error(f"Failed to record failure metrics: {record_e}")

        return {**results, "error": f"Tool 1 failed: {str(e)}", "metrics": metrics.summary()}

    # ==================== Tool 2: detect structure ====================
    try:
        logger.info("TOOL 2: Detecting delimiter and document structure...")
        tool2_start = time.time()

        tool2 = _require_tool("delimiter_detector")
        tool2_response = json.loads(tool2({
            "guid": guid,
            "raw_sample": tool1_response["raw_sample"],
            "detected_format_hint": tool1_response["detected_format_hint"],
            "actual_header_row_index": tool1_response["actual_header_row_index"],
            "sheet_names": tool1_response.get("sheet_names"),
            "total_records": tool1_response.get("total_records"),
            "total_bytes": tool1_response.get("total_bytes"),
        }))

        tool2_end = time.time()

        if tool2_response.get("status") != "success":
            raise Exception(f"Tool 2 failed: {tool2_response.get('error')}")

        # A rejected document is a definite answer, not a failure: nothing about
        # it can be extracted, and the reason is recorded against the guid so
        # the population can be audited later.
        if tool2_response.get("rejected"):
            code = tool2_response.get("rejection_code")
            reason = tool2_response.get("rejection_reason")
            logger.warning(f"⊘ Document rejected [{code}]: {reason}")

            metrics.record_stage(
                "Tool 2: delimiter_detector",
                tool2_start, time.time(), "rejected",
                batch_start_time=batch_start_time,
                rejection_code=code,
            )
            results["stages"]["tool_2"] = tool2_response
            results["rejected"] = True
            results["rejection_code"] = code
            results["rejection_reason"] = reason

            try:
                record_pipeline_run(
                    guid=guid, batch_id=1, source="agentic",
                    stage_metrics=metrics.stages,
                    total_rows_extracted=0, success=False,
                )
            except Exception as record_e:
                logger.error(f"Failed to record rejection metrics: {record_e}")

            return {**results, "metrics": metrics.summary()}

        report = tool2_response.get("metadata_report", {})
        metrics.record_stage(
            "Tool 2: delimiter_detector",
            tool2_start,
            tool2_end,
            "success",
            batch_start_time=batch_start_time,
            delimiter=report.get("delimiter_name"),
            header_field_count=report.get("header_field_count"),
            data_row_count=report.get("data_row_count")
        )

        results["stages"]["tool_2"] = tool2_response
        logger.info(f"✓ Tool 2 success: Delimiter={report.get('delimiter_name')}, "
                   f"Header={report.get('header_field_count')} fields, "
                   f"Rows={report.get('data_row_count')}")

    except Exception as e:
        logger.error(f"✗ Tool 2 failed: {str(e)}")
        metrics.record_error("Tool 2", str(e))

        # Record failure metrics
        try:
            record_pipeline_run(
                guid=guid, batch_id=1, source="agentic",
                stage_metrics=metrics.stages,
                total_rows_extracted=0, success=False
            )
        except Exception as record_e:
            logger.error(f"Failed to record failure metrics: {record_e}")

        return {**results, "error": f"Tool 2 failed: {str(e)}", "metrics": metrics.summary()}

    # ======== Tools 3-5: generate the script, run it, decide if it worked ========
    # A failed extraction goes round again: Tool 4 says what went wrong, Tool 3
    # regenerates with that as context, and the cache is bypassed so the retry
    # cannot be handed back the script that just failed.
    tool3 = _require_tool("generate_parser_script")
    tool4 = _require_tool("sandbox_execute")
    tool5 = _require_tool("evaluate_extraction")

    feedback = None
    attempt = 0
    extracted_rows = []
    passed = False

    while attempt < MAX_EXTRACTION_ATTEMPTS:
        attempt += 1
        label = f" (attempt {attempt})" if attempt > 1 else ""

        # ---------- Tool 3: generate the extraction script ----------
        try:
            logger.info(f"TOOL 3: Generating extraction script{label}...")
            tool3_start = time.time()

            tool3_response = json.loads(tool3({
                "guid": guid,
                "raw_sample": tool1_response["raw_sample"],
                "metadata_report": report,
                "feedback": feedback,
                "attempt": attempt,
            }))
            tool3_end = time.time()

            if tool3_response.get("status") == "skipped":
                logger.warning(f"⊘ Tool 3 skipped: {tool3_response.get('error')}")
                results["rejected"] = True
                results["rejection_reason"] = tool3_response.get("error")
                return {**results, "metrics": metrics.summary()}

            # Generation is not deterministic, so it can fail on a document it
            # would succeed on next time. Previously only a failed *extraction*
            # earned a retry, and a failed generation ended the run — one of the
            # fifty documents was lost that way and succeeded on a rerun.
            if tool3_response.get("status") != "success":
                generation_error = tool3_response.get("error")
                logger.warning(f"✗ Tool 3 could not produce a script: {generation_error}")

                metrics.record_stage(
                    "Tool 3: generate_parser_script",
                    tool3_start, tool3_end, "failed_generation",
                    batch_start_time=batch_start_time,
                    attempt=attempt,
                )

                if attempt < MAX_EXTRACTION_ATTEMPTS:
                    feedback = (
                        f"The previous attempt did not produce usable code: "
                        f"{generation_error}. Write straightforward code and "
                        f"make sure parse_row returns its result."
                    )
                    logger.warning(f"↻ Regenerating: {str(generation_error)[:100]}")
                    continue

                metrics.record_error("Tool 3", str(generation_error))
                _record_failure(guid, metrics)
                results["extraction_passed"] = False
                results["failure_reason"] = f"Could not generate a script: {generation_error}"
                return {**results, "metrics": metrics.summary()}

            generated_code = tool3_response.get("generated_code", {})
            code_len = len(generated_code.get("code", ""))

            metrics.record_stage(
                "Tool 3: generate_parser_script",
                tool3_start, tool3_end, "success",
                batch_start_time=batch_start_time,
                code_length=code_len,
                attempt=attempt,
                cache_hit=tool3_response.get("cache_hit"),
            )
            results["stages"]["tool_3"] = {
                "status": tool3_response["status"],
                "code_length": code_len,
                "attempt": attempt,
                "cache_hit": tool3_response.get("cache_hit"),
            }
            logger.info(f"✓ Tool 3: {code_len} chars, cache_hit={tool3_response.get('cache_hit')}")

        except Exception as e:
            logger.error(f"✗ Tool 3 failed: {str(e)}")
            metrics.record_error("Tool 3", str(e))
            _record_failure(guid, metrics)
            return {**results, "error": f"Tool 3 failed: {str(e)}", "metrics": metrics.summary()}

        # ---------- Tool 4: execute ----------
        try:
            logger.info(f"TOOL 4: Executing script in sandbox{label}...")
            tool4_start = time.time()

            # The full document, not tool1's sample: passing raw_sample here
            # capped extraction at a handful of rows however large the file was.
            tool4_response = json.loads(tool4({
                "guid": guid,
                "generated_code": generated_code.get("code"),
                "body_text": body_text or tool1_response.get("raw_sample"),
                "metadata_report": report,
            }))
            tool4_end = time.time()

            rows_out = len(tool4_response.get("extracted_rows", []))
            metrics.record_stage(
                "Tool 4: sandbox_execute",
                tool4_start, tool4_end,
                "success" if tool4_response.get("status") == "success" else "error",
                batch_start_time=batch_start_time,
                attempt=attempt,
                rows_out=rows_out,
            )
            results["stages"]["tool_4"] = {
                "status": tool4_response.get("status"),
                "attempt": attempt,
                "rows_out": rows_out,
            }

            if tool4_response.get("status") == "success":
                logger.info(f"✓ Tool 4: {rows_out} rows out")
            else:
                # Not fatal: a script that will not run is what a retry fixes,
                # so the evaluator sees the failure and says whether to retry.
                logger.warning(f"Tool 4 execution failed: {tool4_response.get('error')}")

        except Exception as e:
            logger.error(f"✗ Tool 4 failed: {str(e)}")
            metrics.record_error("Tool 4", str(e))
            _record_failure(guid, metrics)
            return {**results, "error": f"Tool 4 failed: {str(e)}", "metrics": metrics.summary()}

        # ---------- Tool 5: did the extraction work? ----------
        try:
            logger.info(f"TOOL 5: Evaluating the extraction{label}...")
            tool5_start = time.time()

            tool5_response = json.loads(tool5({
                "guid": guid,
                "execution_result": tool4_response,
                "metadata_report": report,
                "attempt": attempt,
            }))
            tool5_end = time.time()

            if tool5_response.get("status") != "success":
                raise Exception(f"Tool 5 failed: {tool5_response.get('error')}")

            passed = bool(tool5_response.get("extraction_passed"))
            evaluation = tool5_response.get("evaluation", {})

            metrics.record_stage(
                "Tool 5: evaluate_extraction",
                tool5_start, tool5_end, "success",
                batch_start_time=batch_start_time,
                attempt=attempt,
                extraction_passed=passed,
                records_returned=evaluation.get("records_returned"),
                source_coverage=evaluation.get("source_coverage"),
                valid_row_share=evaluation.get("valid_row_share"),
            )
            results["stages"]["tool_5"] = {
                "extraction_passed": passed,
                "attempt": attempt,
                "failure_reason": tool5_response.get("failure_reason"),
                "evaluation": evaluation,
            }

            if passed:
                extracted_rows = tool4_response.get("extracted_rows", [])
                metrics.total_rows_extracted = len(extracted_rows)
                logger.info(
                    f"✓ Tool 5 PASSED: {evaluation.get('records_returned')} records "
                    f"from {evaluation.get('source_rows_read')} source rows"
                )
                break

            logger.warning(f"✗ Tool 5 FAILED: {tool5_response.get('failure_reason')}")

        except Exception as e:
            logger.error(f"✗ Tool 5 failed: {str(e)}")
            metrics.record_error("Tool 5", str(e))
            _record_failure(guid, metrics)
            return {**results, "error": f"Tool 5 failed: {str(e)}", "metrics": metrics.summary()}

        # Failed, and the evaluator says a retry is worth making: regenerate with
        # the reason attached so the next script can avoid it.
        if tool5_response.get("should_retry"):
            feedback = tool5_response.get("failure_reason")
            logger.warning(f"↻ Reprocessing: {str(feedback)[:120]}")
        else:
            break

    if not passed:
        logger.warning(f"⊘ Extraction did not pass after {attempt} attempt(s)")
        results["extraction_passed"] = False
        results["failure_reason"] = tool5_response.get("failure_reason")
        _record_failure(guid, metrics)
        return {**results, "metrics": metrics.summary()}

    # ==================== Tool 6: load_to_bigquery ====================
    # Only a passing extraction is loaded. Every document goes into one shared
    # table with its own columns carried in a JSON column, so a batch of
    # documents costs one load job rather than one each.
    #
    # A corpus run takes that batching up a level and loads a whole bin in one
    # job, so it asks for the rows back instead of loading here. Loading one
    # document at a time would spend one load job per document against a
    # per-table quota of 1,500 a day — the corpus would stall on the quota long
    # before it ran out of documents.
    if not load:
        results["extraction_passed"] = True
        results["extracted_rows"] = extracted_rows
        results["success"] = True
        results["metrics"] = metrics.summary()
        logger.info(f"Extraction complete, {len(extracted_rows)} row(s) left to the caller to load")
        return results

    try:
        logger.info("TOOL 6: Loading to BigQuery...")
        load_start = time.time()

        tool6 = _require_tool("load_to_bigquery")
        load_response = json.loads(tool6({
            "guid": guid,
            "extracted_rows": extracted_rows,
        }))
        load_end = time.time()

        if load_response.get("status") != "success":
            raise Exception(f"Tool 6 failed: {load_response.get('error')}")

        metrics.record_stage(
            "Tool 6: load_to_bigquery",
            load_start, load_end, "success",
            batch_start_time=batch_start_time,
            rows_loaded=load_response.get("rows_loaded"),
            table=load_response.get("table"),
        )
        results["stages"]["tool_6"] = {
            "status": load_response["status"],
            "table": load_response.get("table"),
            "rows_loaded": load_response.get("rows_loaded"),
            "documents_loaded": load_response.get("documents_loaded"),
        }
        logger.info(
            f"✓ Loaded {load_response.get('rows_loaded')} rows to "
            f"{load_response.get('table')}"
        )

    except Exception as e:
        logger.error(f"✗ Tool 6 failed: {str(e)}")
        metrics.record_error("Tool 6", str(e))
        _record_failure(guid, metrics)
        return {**results, "error": f"Tool 6 failed: {str(e)}", "metrics": metrics.summary()}

    # ==================== Pipeline Complete ====================
    results["success"] = True
    results["metrics"] = metrics.summary()

    logger.info("=" * 80)
    logger.info("PIPELINE COMPLETE")
    logger.info(f"Total duration: {metrics.summary()['total_duration_sec']:.2f} seconds")
    logger.info(f"Total rows extracted: {metrics.total_rows_extracted}")
    logger.info(f"Errors: {len(metrics.errors)}")

    # Record metrics to CSV and JSON (like mosaic's throughput.py)
    try:
        metrics_recording = record_pipeline_run(
            guid=guid,
            batch_id=1,
            source="agentic",
            stage_metrics=metrics.stages,
            total_rows_extracted=metrics.total_rows_extracted,
            success=True,
        )
        logger.info(f"Metrics recorded: {metrics_recording['csv_path']}, {metrics_recording['json_path']}")
        results["metrics_files"] = {
            "csv": metrics_recording["csv_path"],
            "json": metrics_recording["json_path"],
        }
    except Exception as e:
        logger.error(f"Failed to record metrics: {e}")

    return results


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Run agentic extraction pipeline (Tools 1-5) on a document"
    )
    parser.add_argument(
        "guid",
        help="Document GUID to extract (e.g., ddffbdb6-5041-4d65-a744-5a0631a629aa)"
    )
    parser.add_argument(
        "--body-text",
        help="Optional direct body text for testing (skips Tool 1 fetch)"
    )
    parser.add_argument(
        "--log-dir",
        default="logs",
        help="Directory for log files (default: logs/)"
    )
    parser.add_argument(
        "--json-output",
        help="Write results JSON to this file"
    )

    args = parser.parse_args()

    # Setup logging
    log_file = setup_logging(args.guid, args.log_dir)

    logger.info(f"Pipeline invoked with guid={args.guid}")
    logger.info(f"Log file: {log_file}")

    # Run pipeline
    result = run_pipeline(args.guid, args.body_text)

    # Write JSON results if requested
    if args.json_output:
        with open(args.json_output, "w") as f:
            json.dump(result, f, indent=2)
        logger.info(f"Results written to {args.json_output}")

    # Print summary
    print("\n" + "=" * 80)
    print("PIPELINE SUMMARY")
    print("=" * 80)
    print(f"GUID: {args.guid}")
    print(f"Success: {result['success']}")

    if result["success"]:
        metrics = result.get("metrics", {})
        print(f"Duration: {metrics.get('total_duration_sec', 0):.2f} seconds")
        print(f"Rows extracted: {metrics.get('total_rows_extracted', 0)}")

        print("\nStage Timings:")
        for stage_name, stage_metrics in metrics.get("stages", {}).items():
            duration = stage_metrics.get("duration_sec", 0)
            status = stage_metrics.get("status", "unknown")
            print(f"  {stage_name}: {duration:.3f}s ({status})")
    else:
        print(f"Error: {result.get('error')}")

    print(f"\nFull results: {log_file}")
    print("=" * 80)

    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
