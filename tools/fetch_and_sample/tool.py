"""Tool 1: fetch_and_sample

Fetches raw data from glean.drive_files (via mosaic-glean-extraction logic)
or from a source (BigQuery, GCS, local file) and returns a small sample
with metadata for schema inference.

Integrates with mosaic-glean-extraction's proven batch fetching logic for efficiency.
"""
import logging
from typing import Any, Dict, Literal, Optional, Iterator, List
import os

from tools.base import AgentTool, ToolResponse
from extraction.core import config
from extraction.core.bigquery_service import get_bigquery_client
from extraction.core.records import CELL_NEWLINE, SHEET_MARKER, split_records

logger = logging.getLogger(__name__)


class FetchAndSampleTool(AgentTool):
    """Fetch and sample raw data from various sources."""

    # glean's workbook->text flattening markers, defined once in
    # extraction.core.records and re-exported here for callers of this tool.
    SHEET_MARKER = SHEET_MARKER
    CELL_NEWLINE = CELL_NEWLINE

    # The Micro-Slicer's window: the Looker's LLM call needs the document's
    # complete bounding box (where it starts and where it ends), not a spread
    # sample from the middle, so this is a literal head+tail slice rather than
    # sample_size-controlled. Bounded in bytes too, so an enormous document
    # still produces a small, LLM-priced slice.
    MICRO_SLICE_HEAD_LINES = 40
    MICRO_SLICE_TAIL_LINES = 20
    MICRO_SLICE_MAX_BYTES = 4096

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
                "fetch_from_glean": {
                    "type": "boolean",
                    "description": "Fetch documents from glean.drive_files using mosaic-glean-extraction logic (efficient batch queries)",
                    "default": False,
                },
                "source_path": {
                    "type": ["string", "null"],
                    "description": "Source path: BigQuery table (project.dataset.table), GCS path (gs://bucket/path), or local file path. Optional if body_text or fetch_from_glean provided.",
                    "default": None,
                },
                "body_text": {
                    "type": ["string", "null"],
                    "description": "Raw text content (e.g., from glean.drive_files body_text column). If provided, source_path is ignored.",
                    "default": None,
                },
                "guid": {
                    "type": ["string", "null"],
                    "description": "Document GUID (from glean.drive_files) for tracking/metadata. Optional.",
                    "default": None,
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of documents to fetch from glean (when fetch_from_glean=true). Default: 20, max: 1000",
                    "default": 20,
                },
                "sample_size": {
                    "type": "integer",
                    "description": "Number of rows to sample per document (default: 10, max: 100)",
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
                "header_row_index": {
                    "type": ["integer", "null"],
                    "description": "Explicit row index containing headers (0-indexed). If null, auto-detect",
                    "default": None,
                },
                "find_header_heuristic": {
                    "type": "boolean",
                    "description": "Search for headers using heuristics (look for label-like values). Set to true if headers are not on row 0",
                    "default": False,
                },
            },
            "required": [],
            "anyOf": [
                {"required": ["fetch_from_glean"]},
                {"required": ["source_path"]},
                {"required": ["body_text"]},
            ],
        }

    @property
    def output_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["success", "error"]},
                "source_path": {"type": ["string", "null"]},
                "source_type": {"type": "string", "enum": ["bigquery", "gcs", "local_file", "glean_document"]},
                "guid": {"type": ["string", "null"]},
                "raw_sample": {"type": "string"},
                "detected_format_hint": {
                    "type": "string",
                    "enum": ["csv", "json", "pipe", "tab", "space_delimited", "unknown"],
                },
                "first_line_is_header": {"type": "boolean"},
                "actual_header_row_index": {"type": "integer"},
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
            input_data: Dictionary with fetch_from_glean/source_path/body_text and options

        Returns:
            ToolResponse with sample data and metadata
        """
        fetch_from_glean = input_data.get("fetch_from_glean", False)
        source_path = input_data.get("source_path")
        body_text = input_data.get("body_text")
        guid = input_data.get("guid")
        limit = min(input_data.get("limit", 20), 1000)
        sample_size = min(input_data.get("sample_size", 10), 100)
        max_bytes = min(input_data.get("max_bytes", 1048576), 10485760)
        skip_rows = input_data.get("skip_rows", 0)
        encoding = input_data.get("encoding", "utf-8")
        header_row_index = input_data.get("header_row_index")
        find_header_heuristic = input_data.get("find_header_heuristic", False)

        # Determine source type and fetch data
        try:
            # Path 1: Fetch from glean using mosaic-glean-extraction logic (MOST EFFICIENT)
            if fetch_from_glean:
                # Returns iterator of (guid, title, body_text, body_length) tuples
                glean_docs = self._fetch_from_glean_batch(limit)

                # Process each document and return results
                results = []
                for doc in glean_docs:
                    result = self._process_sample(
                        body_text=doc["body_text"],
                        guid=doc["guid"],
                        title=doc.get("title"),
                        sample_size=sample_size,
                        max_bytes=max_bytes,
                        skip_rows=skip_rows,
                        encoding=encoding,
                        header_row_index=header_row_index,
                        find_header_heuristic=find_header_heuristic,
                    )
                    results.append(result)

                # Return first result (or error if no documents)
                if results:
                    return results[0]  # TODO: Consider returning all results
                else:
                    return ToolResponse(status="error", error="No documents found in glean.drive_files")

            # Path 2: Body text provided directly (e.g., from glean.drive_files)
            elif body_text:
                return self._process_sample(
                    body_text=body_text,
                    guid=guid,
                    sample_size=sample_size,
                    max_bytes=max_bytes,
                    skip_rows=skip_rows,
                    encoding=encoding,
                    header_row_index=header_row_index,
                    find_header_heuristic=find_header_heuristic,
                )

            # Path 3: Source path provided (file/GCS/BigQuery table)
            elif source_path:
                return self._process_sample(
                    source_path=source_path,
                    sample_size=sample_size,
                    max_bytes=max_bytes,
                    skip_rows=skip_rows,
                    encoding=encoding,
                    header_row_index=header_row_index,
                    find_header_heuristic=find_header_heuristic,
                )

            else:
                raise ValueError("Either fetch_from_glean, source_path, or body_text must be provided")

        except Exception as e:
            logger.error(f"Fetch error: {str(e)}")
            return ToolResponse(status="error", error=str(e))

    def _process_sample(
        self,
        body_text: Optional[str] = None,
        source_path: Optional[str] = None,
        guid: Optional[str] = None,
        title: Optional[str] = None,
        sample_size: int = 10,
        max_bytes: int = 1048576,
        skip_rows: int = 0,
        encoding: str = "utf-8",
        header_row_index: Optional[int] = None,
        find_header_heuristic: bool = False,
    ) -> ToolResponse:
        """Process a single document sample."""
        try:
            # Every fetcher returns the document's records, not a pre-cut slice,
            # so the sampling below sees the whole document.
            if source_path and not body_text:
                if source_path.startswith("gs://"):
                    records, source_type, total_bytes, sheets = self._fetch_from_gcs(
                        source_path, max_bytes
                    )
                elif "." in source_path and not source_path.startswith("/"):
                    # Assume BigQuery (project.dataset.table format)
                    records, source_type, total_bytes, sheets = self._fetch_from_bigquery(
                        source_path, sample_size, skip_rows
                    )
                else:
                    # Assume local file
                    records, source_type, total_bytes, sheets = self._fetch_from_local_file(
                        source_path, max_bytes, encoding
                    )
            else:
                # body_text provided
                records, source_type, total_bytes, sheets = self._fetch_from_body_text(
                    body_text, max_bytes, encoding
                )

            # Micro-Slicer: the document's complete bounding box (head+tail),
            # not a spread across the middle - see _sample_records.
            sampled, sampled_indices = self._sample_records(records, sample_size, skip_rows)
            sampled = self._fit_to_budget(
                sampled, min(max_bytes, self.MICRO_SLICE_MAX_BYTES), encoding
            )
            raw_sample = "\n".join(sampled)

            if header_row_index is not None:
                actual_header_row_index = header_row_index
            elif find_header_heuristic:
                actual_header_row_index = self._find_header_row(sampled)
            else:
                actual_header_row_index = 0

            # Get the header line
            header_line = sampled[actual_header_row_index] if actual_header_row_index < len(sampled) else ""
            first_line_is_header = self._detect_header(header_line)
            detected_format = self._detect_format_from_sample(sampled)

            return ToolResponse(
                status="success",
                source_path=source_path,
                source_type=source_type,
                guid=guid,
                raw_sample=raw_sample,
                total_records=len(records),
                sampled_record_indices=sampled_indices,
                sheet_names=sheets,
                detected_format_hint=detected_format,
                first_line_is_header=first_line_is_header,
                actual_header_row_index=actual_header_row_index,
                encoding=encoding,
                total_bytes=total_bytes,
                sample_size=len(sampled),
                byte_sample_size=len(raw_sample.encode(encoding)),
                error=None,
            )

        except Exception as e:
            logger.error(f"Process sample error: {str(e)}")
            return ToolResponse(status="error", error=str(e))

    def _fetch_from_glean_batch(self, limit: int = 20) -> Iterator[Dict]:
        """Fetch documents from glean.drive_files using mosaic-glean-extraction logic.

        This is the MOST EFFICIENT way to fetch documents - single batch query,
        proper filtering (triage_category, size), proven production logic.

        Yields:
            Iterator of dicts with: guid, title, body_text, body_length
        """
        try:
            client = get_bigquery_client()

            # Use EXACT same query as mosaic-glean-extraction for consistency
            query = f"""
            SELECT
                id as guid,
                title,
                body_text,
                LENGTH(body_text) as body_length
            FROM `{config.SOURCE_PROJECT}.{config.SOURCE_TABLE}`
            WHERE triage_category = '{config.SOURCE_TRIAGE_CATEGORY}'
                AND body_text IS NOT NULL
                AND LENGTH(body_text) > 100
            ORDER BY RAND()
            LIMIT {limit}
            """

            logger.info(f"Fetching {limit} documents from glean using mosaic logic")
            results = client.query(query).result()

            for row in results:
                yield {
                    "guid": row.guid,
                    "title": row.title,
                    "body_text": row.body_text,
                    "body_length": row.body_length,
                }

            logger.info(f"Successfully fetched documents from glean")

        except Exception as e:
            logger.error(f"Failed to fetch from glean: {e}")
            raise

    def _fetch_from_body_text(
        self, body_text: str, max_bytes: int, encoding: str
    ) -> tuple[List[str], str, int, List[str]]:
        """Split raw text (glean.drive_files body_text) into records.

        Note max_bytes is not applied here: sampling needs to reach the end of
        the document, and truncating first would confine the sample to the top.
        """
        total_bytes = len(body_text.encode(encoding))
        records, sheets = self._split_records(body_text)
        return records, "glean_document", total_bytes, sheets

    @staticmethod
    def _fit_to_budget(sampled: List[str], max_bytes: int, encoding: str) -> List[str]:
        """Trim the sample to max_bytes without dropping records.

        max_bytes bounds what is carried forward, not what is read: truncating
        the document before sampling would confine the sample to its opening
        rows. Every sampled record is kept, each shortened to an equal share, so
        a single enormous record cannot crowd the others out.
        """
        if not sampled or len(("\n".join(sampled)).encode(encoding)) <= max_bytes:
            return sampled

        per_record = max(1, max_bytes // len(sampled) - 1)
        trimmed = []
        for record in sampled:
            encoded = record.encode(encoding)
            if len(encoded) > per_record:
                record = encoded[:per_record].decode(encoding, errors="ignore")
            trimmed.append(record)
        return trimmed

    def _split_records(self, body_text: str) -> tuple[List[str], List[str]]:
        """Split a flattened workbook into records and worksheet names.

        Delegates to extraction.core.records so that this tool and the sandbox
        that executes generated code split rows the same way.

        Returns:
            Tuple of (records, sheet_names)
        """
        return split_records(body_text)

    def _sample_records(
        self, records: List[str], sample_size: int, skip_rows: int = 0
    ) -> tuple[List[str], List[int]]:
        """Micro-Slicer: a literal head+tail window, the document's bounding box.

        Returns the opening MICRO_SLICE_HEAD_LINES records and the closing
        MICRO_SLICE_TAIL_LINES records, rather than a sample spread across the
        whole document. The Looker's structural-inspection call needs to see
        both ends explicitly: a title block or comment rows above the header
        at the top, and any footer (totals, page markers) at the bottom - a
        spread sample can miss either. sample_size is accepted for the input
        contract but no longer drives how much is taken; a document smaller
        than the head+tail window is returned in full either way.

        Returns:
            Tuple of (sampled_records, their indices in the original document)
        """
        body = records[skip_rows:]
        if not body:
            return [], []

        window = self.MICRO_SLICE_HEAD_LINES + self.MICRO_SLICE_TAIL_LINES
        if len(body) <= window:
            return body, [i + skip_rows for i in range(len(body))]

        head_n = min(self.MICRO_SLICE_HEAD_LINES, len(body))
        tail_n = min(self.MICRO_SLICE_TAIL_LINES, len(body) - head_n)

        indices = sorted(set(range(head_n)) | set(range(len(body) - tail_n, len(body))))
        return [body[i] for i in indices], [i + skip_rows for i in indices]

    def _fetch_from_bigquery(
        self, table_id: str, sample_size: int, skip_rows: int
    ) -> tuple[List[str], str, int, List[str]]:
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

        total_bytes = len("\n".join(lines).encode("utf-8"))

        return lines, "bigquery", total_bytes, []

    def _fetch_from_gcs(
        self, gcs_path: str, max_bytes: int
    ) -> tuple[List[str], str, int, List[str]]:
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

        records, sheets = self._split_records(content)
        return records, "gcs", total_bytes, sheets

    def _fetch_from_local_file(
        self, file_path: str, max_bytes: int, encoding: str
    ) -> tuple[List[str], str, int, List[str]]:
        """Fetch sample from local file."""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        total_bytes = os.path.getsize(file_path)

        with open(file_path, "r", encoding=encoding) as f:
            content = f.read()

        records, sheets = self._split_records(content)
        return records, "local_file", total_bytes, sheets

    def _find_header_row(self, lines: list) -> int:
        """Find the row that looks like a header using heuristics.

        Searches first 10 rows for one with label-like values.
        Returns the index of the best match, defaults to 0.
        """
        if not lines:
            return 0

        best_score = -1
        best_index = 0

        # Search first 10 rows for header
        for i, line in enumerate(lines[:10]):
            if not line.strip():
                continue

            score = self._score_as_header(line)
            if score > best_score:
                best_score = score
                best_index = i

        return best_index

    def _score_as_header(self, line: str) -> float:
        """Score how likely a line is to be a header row.

        Higher score = more likely to be a header.
        Looks for: common keywords, non-numeric values, consistent structure.
        """
        if not line.strip():
            return -1

        tokens = line.split(",")
        if len(tokens) < 2:
            tokens = line.split("\t")
        if len(tokens) < 2:
            tokens = line.split("|")
        if len(tokens) < 2:
            tokens = line.split()

        if not tokens:
            return -1

        score = 0.0
        label_count = 0

        header_keywords = {
            "id", "name", "title", "date", "time", "email", "phone",
            "value", "count", "amount", "price", "description", "comment",
            "address", "city", "state", "zip", "country", "status"
        }

        for token in tokens:
            token_lower = token.lower().strip()
            if not token_lower:
                continue

            # Check for numeric value (not a label)
            try:
                float(token_lower)
                score -= 0.5
                continue
            except ValueError:
                pass

            # Check for header keywords
            if any(kw in token_lower for kw in header_keywords):
                score += 2.0
                label_count += 1
            # Check if looks like a label (all letters, underscores, hyphens)
            elif all(c.isalpha() or c in "_-" for c in token_lower):
                score += 0.5
                label_count += 1
            else:
                score -= 0.2

        # Bonus if most tokens look like labels
        if label_count >= len(tokens) * 0.7:
            score += 1.0

        return score

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
    def _detect_format_from_sample(records: List[str]) -> str:
        """Detect the delimiter from the sample as a whole.

        A single record is not enough: the opening record of a worksheet is often
        a title or a merged cell, which carries no delimiter at all. The winning
        delimiter is the one that splits the most records consistently.
        """
        if not records:
            return "unknown"

        stripped = [r.strip() for r in records if r.strip()]
        if not stripped:
            return "unknown"

        if sum(1 for r in stripped if r.startswith(("{", "["))) > len(stripped) / 2:
            return "json"

        best_format, best_score = "unknown", 0
        for delimiter, name in ((",", "csv"), ("|", "pipe"), ("\t", "tab")):
            # Score on records actually split, not on total occurrences, so one
            # comma-heavy prose cell cannot outvote the real delimiter.
            score = sum(1 for r in stripped if delimiter in r)
            if score > best_score:
                best_format, best_score = name, score

        if best_score >= max(2, len(stripped) * 0.5):
            return best_format
        if best_score > 0:
            return best_format

        if sum(1 for r in stripped if " " in r) > len(stripped) / 2:
            return "space_delimited"

        return "unknown"

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
