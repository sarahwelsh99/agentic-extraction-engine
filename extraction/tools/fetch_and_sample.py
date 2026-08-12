"""Tool 1: Fetch and Sample

Fetches raw data from a source (BigQuery, GCS, or local file) and returns
a small sample with metadata.
"""
import json
import logging
from typing import TypedDict, Literal, Optional
from datetime import datetime, timezone
from io import StringIO

logger = logging.getLogger(__name__)


class FetchAndSampleInput(TypedDict):
    """Input schema for fetch_and_sample tool."""
    source_path: str                          # BigQuery (project.dataset.table), GCS (gs://...), or local path
    sample_size: int                          # Rows to sample (default: 10, max: 100)
    max_bytes: int                            # Max bytes to fetch (default: 1MB)
    skip_rows: int                            # Rows to skip (default: 0)
    encoding: str                             # Encoding hint (default: "utf-8")


class FetchAndSampleResponse(TypedDict):
    """Response schema for fetch_and_sample tool."""
    status: Literal["success", "error"]
    source_path: Optional[str]
    source_type: Optional[Literal["bigquery", "gcs", "local_file"]]
    total_rows: Optional[int]
    total_bytes: Optional[int]
    sample_size: Optional[int]
    raw_sample: Optional[str]
    encoding: Optional[str]
    first_line_is_header: Optional[bool]
    detected_format_hint: Optional[str]
    byte_sample_size: Optional[int]
    error: Optional[str]
    timestamp: str


def fetch_and_sample(input_data: FetchAndSampleInput) -> str:
    """
    Fetch raw data from a source and return a small sample with metadata.

    Connects to BigQuery, GCS, or local files and extracts a sample of raw data
    for schema inference and profiling.

    Args:
        input_data: Dictionary with source path and sampling options.
            Required keys:
            - source_path: str - BigQuery table (project.dataset.table),
                                 GCS path (gs://bucket/path), or local file
            Optional keys:
            - sample_size: int - Rows to sample (default: 10, max: 100)
            - max_bytes: int - Max bytes to fetch (default: 1MB, max: 10MB)
            - skip_rows: int - Rows to skip before sampling (default: 0)
            - encoding: str - Encoding hint (default: "utf-8")

    Returns:
        JSON string with sample data and metadata.

    Example:
        >>> input_data = {
        ...     "source_path": "my-project.my_dataset.my_table",
        ...     "sample_size": 10,
        ...     "max_bytes": 1048576,
        ...     "skip_rows": 0,
        ...     "encoding": "utf-8"
        ... }
        >>> response_json = fetch_and_sample(input_data)
        >>> response = json.loads(response_json)
        >>> assert response["status"] == "success"
        >>> assert response["raw_sample"] is not None
    """
    response: FetchAndSampleResponse = {
        "status": "error",
        "source_path": input_data.get("source_path"),
        "source_type": None,
        "total_rows": None,
        "total_bytes": None,
        "sample_size": None,
        "raw_sample": None,
        "encoding": None,
        "first_line_is_header": None,
        "detected_format_hint": None,
        "byte_sample_size": None,
        "error": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        # Validate input
        source_path = input_data.get("source_path")
        if not source_path:
            response["error"] = "source_path is required"
            return json.dumps(response)

        sample_size = input_data.get("sample_size", 10)
        if sample_size > 100:
            sample_size = 100
        if sample_size < 1:
            sample_size = 10

        max_bytes = input_data.get("max_bytes", 1048576)  # 1MB default
        if max_bytes > 10485760:  # 10MB max
            max_bytes = 10485760

        skip_rows = input_data.get("skip_rows", 0)
        encoding = input_data.get("encoding", "utf-8")

        # Determine source type and fetch data
        if source_path.startswith("gs://"):
            raw_sample, source_type, total_bytes = _fetch_from_gcs(
                source_path, sample_size, max_bytes, skip_rows
            )
        elif "." in source_path and not source_path.startswith("/"):
            # Assume BigQuery (project.dataset.table format)
            raw_sample, source_type, total_bytes = _fetch_from_bigquery(
                source_path, sample_size, max_bytes, skip_rows
            )
        else:
            # Assume local file
            raw_sample, source_type, total_bytes = _fetch_from_local_file(
                source_path, sample_size, max_bytes, skip_rows, encoding
            )

        # Detect if first line is header
        first_line = raw_sample.split("\n")[0] if raw_sample else ""
        first_line_is_header = _detect_header(first_line)

        # Detect format hint
        detected_format = _detect_format(first_line)

        response.update({
            "status": "success",
            "source_type": source_type,
            "total_rows": None,  # Only available for BigQuery
            "total_bytes": total_bytes,
            "sample_size": len(raw_sample.split("\n")) - 1,  # Subtract header
            "raw_sample": raw_sample,
            "encoding": encoding,
            "first_line_is_header": first_line_is_header,
            "detected_format_hint": detected_format,
            "byte_sample_size": len(raw_sample.encode(encoding)),
            "error": None,
        })

    except Exception as e:
        logger.error(f"fetch_and_sample error: {str(e)}")
        response["error"] = str(e)
        response["status"] = "error"

    return json.dumps(response)


def _fetch_from_bigquery(
    table_id: str, sample_size: int, max_bytes: int, skip_rows: int
) -> tuple[str, str, int]:
    """Fetch sample from BigQuery table."""
    try:
        from extraction.core.bigquery_service import get_bigquery_client
    except ImportError:
        raise ImportError("BigQuery client not available. Install google-cloud-bigquery.")

    client = get_bigquery_client()

    # Query to get sample rows
    query = f"""
    SELECT *
    FROM `{table_id}`
    LIMIT {sample_size}
    OFFSET {skip_rows}
    """

    query_job = client.query(query)
    rows = query_job.result()

    # Convert to CSV-like format
    if rows.total_rows == 0:
        raise ValueError(f"No rows found in {table_id}")

    # Get column names
    columns = [field.name for field in rows.schema]
    lines = [",".join(columns)]

    # Add row data
    for row in rows:
        line = ",".join(str(val) if val is not None else "" for val in row.values())
        lines.append(line)

    raw_sample = "\n".join(lines)
    total_bytes = len(raw_sample.encode("utf-8"))

    return raw_sample, "bigquery", total_bytes


def _fetch_from_gcs(
    gcs_path: str, sample_size: int, max_bytes: int, skip_rows: int
) -> tuple[str, str, int]:
    """Fetch sample from GCS file."""
    try:
        from google.cloud import storage
    except ImportError:
        raise ImportError("GCS client not available. Install google-cloud-storage.")

    # Parse GCS path
    parts = gcs_path.replace("gs://", "").split("/", 1)
    bucket_name = parts[0]
    file_path = parts[1] if len(parts) > 1 else ""

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(file_path)

    # Download file
    content = blob.download_as_text(encoding="utf-8")
    total_bytes = blob.size or len(content.encode("utf-8"))

    # Extract sample
    lines = content.split("\n")
    sample_lines = lines[skip_rows : skip_rows + sample_size + 1]
    raw_sample = "\n".join(sample_lines)

    return raw_sample, "gcs", total_bytes


def _fetch_from_local_file(
    file_path: str, sample_size: int, max_bytes: int, skip_rows: int, encoding: str
) -> tuple[str, str, int]:
    """Fetch sample from local file."""
    import os

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    # Get file size
    total_bytes = os.path.getsize(file_path)

    # Read file
    with open(file_path, "r", encoding=encoding) as f:
        lines = f.readlines()

    # Extract sample
    sample_lines = lines[skip_rows : skip_rows + sample_size + 1]
    raw_sample = "".join(sample_lines).rstrip("\n")

    return raw_sample, "local_file", total_bytes


def _detect_header(first_line: str) -> bool:
    """Detect if first line looks like a header."""
    if not first_line:
        return False

    # Heuristics: headers often have lowercase words, no special chars
    tokens = first_line.split(",")[0].split()  # Just check first field
    if not tokens:
        return False

    first_token = tokens[0].lower()

    # Common header keywords
    header_keywords = {"id", "name", "title", "date", "time", "email", "phone", "value", "count"}
    if any(kw in first_token for kw in header_keywords):
        return True

    # If it looks like a word (not a number), likely header
    try:
        float(first_token)
        return False
    except ValueError:
        return True


def _detect_format(first_line: str) -> str:
    """Detect file format from first line."""
    if not first_line:
        return "unknown"

    # Check for JSON
    if first_line.strip().startswith("{") or first_line.strip().startswith("["):
        return "json"

    # Check for pipe-delimited
    if "|" in first_line:
        return "pipe"

    # Check for tab-delimited
    if "\t" in first_line:
        return "tab"

    # Check for comma-delimited
    if "," in first_line:
        return "csv"

    # Check for space-delimited
    if " " in first_line and "," not in first_line:
        return "space_delimited"

    return "unknown"
