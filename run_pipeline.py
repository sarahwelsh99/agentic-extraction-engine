#!/usr/bin/env python3
"""Orchestration script for the agentic extraction pipeline.

Drives extraction.core.pipeline_agent.PipelineAgent (Looker -> Thinker ->
Tester -> Eval) for one document, then delivers a passing extraction with
Tool 6 (write_parquet_to_gcs). This module owns logging, metrics recording,
and the CLI; the state machine itself lives in pipeline_agent.py.

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
from typing import Any, Dict

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
from extraction.core.pipeline_agent import PipelineAgent, PipelineState


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


# Maps PipelineAgent's own stage names to the "Tool N: name" labels
# metrics_recorder.py and downstream dashboards already key on.
_STAGE_LABELS = {
    "look_slice": "Tool 1: fetch_and_sample",
    "look_inspect": "Tool 2: structural_inspector",
    "think": "Tool 3: generate_parser_script",
    "test": "Tool 4: sandbox_execute",
    "eval": "Tool 5: evaluate_extraction",
}


def _stage_metadata(stage: str, response: Dict[str, Any]) -> Dict[str, Any]:
    """The same per-stage fields the old inline pipeline used to record."""
    if stage == "look_slice":
        return {
            "format": response.get("detected_format_hint"),
            "total_bytes": response.get("total_bytes"),
            "sample_size": response.get("sample_size"),
        }
    if stage == "look_inspect":
        report = response.get("metadata_report") or {}
        return {
            "delimiter": report.get("delimiter_name"),
            "header_field_count": report.get("header_field_count"),
            "data_row_count": report.get("data_row_count"),
        }
    if stage == "think":
        code = response.get("generated_code") or {}
        return {
            "code_length": len(code.get("code", "")),
            "cache_hit": response.get("cache_hit"),
        }
    if stage == "test":
        return {"rows_out": len(response.get("extracted_rows", []))}
    if stage == "eval":
        evaluation = response.get("evaluation") or {}
        return {
            "extraction_passed": response.get("extraction_passed"),
            "records_returned": evaluation.get("records_returned"),
            "source_coverage": evaluation.get("source_coverage"),
            "valid_row_share": evaluation.get("valid_row_share"),
        }
    return {}


def _record_stage_log(metrics: PipelineMetrics, state: PipelineState,
                      batch_start_time: float) -> None:
    """Feed the agent's own stage_log into this run's PipelineMetrics."""
    for entry in state.stage_log:
        label = _STAGE_LABELS.get(entry["stage"], entry["stage"])
        status = "success" if entry["status"] == "success" else "error"
        metrics.record_stage(
            label, entry["start"], entry["end"], status,
            batch_start_time=batch_start_time,
            attempt=entry["attempt"],
            **_stage_metadata(entry["stage"], entry["response"]),
        )
        if status != "success":
            metrics.record_error(label, entry["response"].get("error"))


def run_pipeline(guid: str, body_text: str = None, load: bool = True) -> dict:
    """Run the complete extraction pipeline: the agent loop, then delivery.

    Args:
        guid: Document GUID to extract
        body_text: Optional direct body text (for testing)
        load: Write a passing extraction with Tool 6. Set False to have the
            rows returned under "extracted_rows" instead, so a caller can
            write a whole batch of documents in one job.

    Returns:
        Dict with pipeline results and metrics
    """
    batch_start_time = time.time()
    metrics = PipelineMetrics()
    results = {"guid": guid, "success": False, "stages": {}}

    logger.info(f"Starting pipeline for guid: {guid}")
    logger.info("=" * 80)

    agent = PipelineAgent(guid, body_text=body_text)
    state = agent.run()
    _record_stage_log(metrics, state, batch_start_time)
    results["stages"] = {e["stage"]: e["response"] for e in state.stage_log}

    if state.status == "rejected":
        logger.warning(f"⊘ Document rejected [{state.rejection_code}]: {state.rejection_reason}")
        results["rejected"] = True
        results["rejection_code"] = state.rejection_code
        results["rejection_reason"] = state.rejection_reason
        _record_failure(guid, metrics)
        return {**results, "metrics": metrics.summary()}

    if state.status != "success":
        logger.warning(f"⊘ Extraction did not pass after {state.retry_count} attempt(s)")
        results["extraction_passed"] = False
        results["failure_reason"] = state.failure_reason
        _record_failure(guid, metrics)
        return {**results, "metrics": metrics.summary()}

    logger.info(
        f"✓ Extraction PASSED after {state.retry_count} attempt(s): "
        f"{len(state.extracted_rows)} row(s)"
    )
    metrics.total_rows_extracted = len(state.extracted_rows)

    # ==================== Deliver: write_parquet_to_gcs ====================
    # Only a passing extraction is written. A corpus run batches delivery
    # across a whole bin instead of one document at a time (see
    # run_corpus.py), so it asks for the rows back rather than writing here.
    if not load:
        results["extraction_passed"] = True
        results["extracted_rows"] = state.extracted_rows
        results["success"] = True
        results["metrics"] = metrics.summary()
        logger.info(f"Extraction complete, {len(state.extracted_rows)} row(s) left to the caller to write")
        return results

    try:
        logger.info("Delivering: writing to GCS as Parquet...")
        write_start = time.time()

        tool6 = _require_tool("write_parquet_to_gcs")
        write_response = json.loads(tool6({
            "guid": guid,
            "extracted_rows": state.extracted_rows,
        }))
        write_end = time.time()

        if write_response.get("status") != "success":
            raise Exception(f"Tool 6 failed: {write_response.get('error')}")

        metrics.record_stage(
            "Tool 6: write_parquet_to_gcs",
            write_start, write_end, "success",
            batch_start_time=batch_start_time,
            rows_written=write_response.get("rows_written"),
            bucket=write_response.get("bucket"),
        )
        results["stages"]["deliver"] = {
            "status": write_response["status"],
            "bucket": write_response.get("bucket"),
            "prefix": write_response.get("prefix"),
            "rows_written": write_response.get("rows_written"),
            "documents_written": write_response.get("documents_written"),
        }
        logger.info(
            f"✓ Wrote {write_response.get('rows_written')} rows to "
            f"gs://{write_response.get('bucket')}/{write_response.get('prefix')}/"
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
        description="Run the agentic extraction pipeline on a document"
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
