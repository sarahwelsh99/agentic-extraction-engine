"""Tool 1: fetch_and_sample

Fetches raw data from a source (BigQuery, GCS, or local file) and returns
a small sample with metadata for schema inference.
"""
import logging
from typing import Any, Dict, Literal, Optional
import os

from tools.base import AgentTool, ToolResponse

logger = logging.getLogger(__name__)


class FetchAndSampleTool(AgentTool):
    """Fetch and sample raw data from various sources."""

    @property
    def name(self) -> str:
        return "fetch_and_sample"

    @property
    def description(self) -> str:
        return "Fetch raw data from a source (BigQuery, GCS, local file) and return sample with metadata"

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "source_path": {
                    "type": "string",
                    "description": "Source path: BigQuery table (project.dataset.table), GCS path (gs://bucket/path), or local file path",
                },
                "sample_size": {
                    "type": "integer",
                    "description": "Number of rows to sample (default: 10, max: 100)",
                    "default": 10,
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "Maximum bytes to fetch (default: 1MB, max: 10MB)",
                    "default": 1048576,
                },
                "skip_rows": {
                    "type": "integer",
                    "description": "Number of rows to skip before sampling (default: 0)",
                    "default": 0,
                },
                "encoding": {
                    "type": "string",
                    "description": "Text encoding (default: utf-8)",
                    "default": "utf-8",
                },
            },
            "required": ["source_path"],
        }

    @property
    def output_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["success", "error"]},
                "source_path": {"type": "string"},
                "source_type": {"type": "string", "enum": ["bigquery", "gcs", "local_file"]},
                "raw_sample": {"type": "string"},
                "detected_format_hint": {
                    "type": "string",
                    "enum": ["csv", "json", "pipe", "tab", "space_delimited", "unknown"],
                },
                "first_line_is_header": {"type": "boolean"},
                "encoding": {"type": "string"},
                "total_bytes": {"type": "integer"},
                "sample_size": {"type": "integer"},
                "byte_sample_size": {"type": "integer"},
                "error": {"type": ["string", "null"]},
                "timestamp": {"type": "string"},
            },
            "required": ["status", "error", "timestamp"],
        }

    def execute(self, input_data: Dict[str, Any]) -> ToolResponse:
        """Execute fetch and sample.

        Args:
            input_data: Dictionary with source_path and options

        Returns:
            ToolResponse with sample data and metadata
        """
        source_path = input_data.get("source_path")
        sample_size = min(input_data.get("sample_size", 10), 100)
        max_bytes = min(input_data.get("max_bytes", 1048576), 10485760)
        skip_rows = input_data.get("skip_rows", 0)
        encoding = input_data.get("encoding", "utf-8")

        # Determine source type and fetch data
        try:
            if source_path.startswith("gs://"):
                raw_sample, source_type, total_bytes = self._fetch_from_gcs(
                    source_path, sample_size, max_bytes, skip_rows
                )
            elif "." in source_path and not source_path.startswith("/"):
                # Assume BigQuery (project.dataset.table format)
                raw_sample, source_type, total_bytes = self._fetch_from_bigquery(
                    source_path, sample_size, max_bytes, skip_rows
                )
            else:
                # Assume local file
                raw_sample, source_type, total_bytes = self._fetch_from_local_file(
                    source_path, sample_size, max_bytes, skip_rows, encoding
                )

            # Detect metadata
            first_line = raw_sample.split("\n")[0] if raw_sample else ""
            first_line_is_header = self._detect_header(first_line)
            detected_format = self._detect_format(first_line)

            return ToolResponse(
                status="success",
                source_path=source_path,
                source_type=source_type,
                raw_sample=raw_sample,
                detected_format_hint=detected_format,
                first_line_is_header=first_line_is_header,
                encoding=encoding,
                total_bytes=total_bytes,
                sample_size=len(raw_sample.split("\n")) - 1,
                byte_sample_size=len(raw_sample.encode(encoding)),
                error=None,
            )

        except Exception as e:
            logger.error(f"Fetch error: {str(e)}")
            return ToolResponse(status="error", error=str(e))

    def _fetch_from_bigquery(
        self, table_id: str, sample_size: int, max_bytes: int, skip_rows: int
    ) -> tuple[str, str, int]:
        """Fetch sample from BigQuery table."""
        try:
            from extraction.core.bigquery_service import get_bigquery_client
        except ImportError:
            raise ImportError("BigQuery client not available")

        client = get_bigquery_client()
        query = f"SELECT * FROM `{table_id}` LIMIT {sample_size} OFFSET {skip_rows}"

        query_job = client.query(query)
        rows = query_job.result()

        if rows.total_rows == 0:
            raise ValueError(f"No rows found in {table_id}")

        columns = [field.name for field in rows.schema]
        lines = [",".join(columns)]

        for row in rows:
            line = ",".join(str(val) if val is not None else "" for val in row.values())
            lines.append(line)

        raw_sample = "\n".join(lines)
        total_bytes = len(raw_sample.encode("utf-8"))

        return raw_sample, "bigquery", total_bytes

    def _fetch_from_gcs(
        self, gcs_path: str, sample_size: int, max_bytes: int, skip_rows: int
    ) -> tuple[str, str, int]:
        """Fetch sample from GCS file."""
        try:
            from google.cloud import storage
        except ImportError:
            raise ImportError("GCS client not available")

        parts = gcs_path.replace("gs://", "").split("/", 1)
        bucket_name = parts[0]
        file_path = parts[1] if len(parts) > 1 else ""

        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(file_path)

        content = blob.download_as_text(encoding="utf-8")
        total_bytes = blob.size or len(content.encode("utf-8"))

        lines = content.split("\n")
        sample_lines = lines[skip_rows : skip_rows + sample_size + 1]
        raw_sample = "\n".join(sample_lines)

        return raw_sample, "gcs", total_bytes

    def _fetch_from_local_file(
        self, file_path: str, sample_size: int, max_bytes: int, skip_rows: int, encoding: str
    ) -> tuple[str, str, int]:
        """Fetch sample from local file."""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        total_bytes = os.path.getsize(file_path)

        with open(file_path, "r", encoding=encoding) as f:
            lines = f.readlines()

        sample_lines = lines[skip_rows : skip_rows + sample_size + 1]
        raw_sample = "".join(sample_lines).rstrip("\n")

        return raw_sample, "local_file", total_bytes

    @staticmethod
    def _detect_header(first_line: str) -> bool:
        """Detect if first line looks like a header."""
        if not first_line:
            return False

        tokens = first_line.split(",")[0].split()
        if not tokens:
            return False

        first_token = tokens[0].lower()
        header_keywords = {"id", "name", "title", "date", "time", "email", "phone", "value", "count"}

        if any(kw in first_token for kw in header_keywords):
            return True

        try:
            float(first_token)
            return False
        except ValueError:
            return True

    @staticmethod
    def _detect_format(first_line: str) -> str:
        """Detect file format from first line."""
        if not first_line:
            return "unknown"

        if first_line.strip().startswith("{") or first_line.strip().startswith("["):
            return "json"
        if "|" in first_line:
            return "pipe"
        if "\t" in first_line:
            return "tab"
        if "," in first_line:
            return "csv"
        if " " in first_line and "," not in first_line:
            return "space_delimited"

        return "unknown"
