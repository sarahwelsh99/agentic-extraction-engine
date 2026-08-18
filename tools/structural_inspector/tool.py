"""Tool 2: structural inspector — the LLM half of the Looker.

Replaces the earlier regex/heuristic delimiter_detector (archived at
retired/delimiter_detector/, superseded because it could only report a single
delimiter/header/width and had no notion of a footer, null tokens, or
multi-line rows). This tool asks the model to read the document's structure
directly from the bounded head+tail slice the Micro-Slicer (fetch_and_sample)
already produced, and returns a richer spec: not just where the header is, but
where the data ends, what a null looks like, and what to watch out for.

Uses LocalLLMClient.chat(json_schema=...) (extraction/core/llm_service.py) so
the reply is guaranteed-parseable JSON — no fence-stripping or "find the last
class definition" recovery the way generate_parser_script needs for Python
code.

Because Tool 3 (generate_parser_script) and Tool 4 (sandbox_execute) still
expect the older flat report shape (delimiter, header_row_index,
modal_field_count, ...), _to_metadata_report() derives that view from the
richer spec, so neither tool's contract has to change. The rich spec itself is
kept as-is under "looker_spec" for anything that wants it directly.

Known gap: delimiter_type values other than comma/pipe/tab/semicolon
(multi_space, fixed_width, other) have no single-character equivalent Tool 4's
csv.reader-based row splitting can use, and are not yet handled end to end;
the adapter falls back to comma rather than fail closed. Likewise
data_anomalies (has_multiline_rows, contains_inline_summaries) are reported
but nothing downstream acts on them yet - see docs/ARCHITECTURE.md.
"""

import json
import logging
from collections import Counter
from typing import Any, Dict, List, Optional

from extraction.core import config
from extraction.core.llm_service import LocalLLMClient

logger = logging.getLogger(__name__)


RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "layout_type": {
            "type": "string",
            "enum": ["tabular_delimited", "fixed_width", "prose_or_report", "no_data"],
        },
        "head_bounds": {
            "type": "object",
            "properties": {
                "has_header": {"type": "boolean"},
                "header_line_index": {"type": "integer"},
                "data_start_line_index": {"type": "integer"},
                "inferred_columns": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "has_header", "header_line_index",
                "data_start_line_index", "inferred_columns",
            ],
            "additionalProperties": False,
        },
        "tail_bounds": {
            "type": "object",
            "properties": {
                "has_footer": {"type": "boolean"},
                "footer_start_from_bottom": {"type": "integer"},
                "footer_patterns": {"type": "array", "items": {"type": "string"}},
                "data_end_strategy": {"type": "string", "enum": ["skipfooter", "none"]},
            },
            "required": [
                "has_footer", "footer_start_from_bottom",
                "footer_patterns", "data_end_strategy",
            ],
            "additionalProperties": False,
        },
        "format_spec": {
            "type": "object",
            "properties": {
                "delimiter_type": {
                    "type": "string",
                    "enum": ["comma", "pipe", "tab", "semicolon", "multi_space",
                             "fixed_width", "other"],
                },
                "delimiter_regex": {"type": "string"},
                "quote_char": {"type": ["string", "null"]},
                "null_values": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["delimiter_type", "delimiter_regex", "quote_char", "null_values"],
            "additionalProperties": False,
        },
        "data_anomalies": {
            "type": "object",
            "properties": {
                "contains_inline_summaries": {"type": "boolean"},
                "has_multiline_rows": {"type": "boolean"},
            },
            "required": ["contains_inline_summaries", "has_multiline_rows"],
            "additionalProperties": False,
        },
    },
    "required": ["layout_type", "head_bounds", "tail_bounds", "format_spec", "data_anomalies"],
    "additionalProperties": False,
}

# Only these have a single character csv.reader can split on. Anything else
# (multi_space, fixed_width, other) has no equivalent yet - see module
# docstring's "Known gap".
_DELIMITER_CHAR_BY_TYPE = {"comma": ",", "pipe": "|", "tab": "\t", "semicolon": ";"}

REJECT_NO_DATA = "NO_DATA_ROWS"
REJECT_NOT_TABULAR = "NOT_TABULAR"


class StructuralInspectorTool:
    """Ask the model to read a document's layout from its head+tail slice."""

    name = "structural_inspector"
    description = "LLM-based structural spec: header/footer bounds, delimiter, null values"

    MAX_TOKENS = 800

    def __init__(self):
        self.client = LocalLLMClient()
        self.max_retries = config.VLLM_MAX_RETRIES

    def __call__(self, inputs: Dict[str, Any]) -> str:
        """Produce a looker_spec (and a derived metadata_report) for one document.

        Args:
            inputs: {
                "raw_sample": micro-sliced head+tail text from fetch_and_sample,
                "guid": optional,
                "sheet_names": optional, from Tool 1,
                "total_records": optional, whole-document count from Tool 1,
                "total_bytes": optional, whole-document size from Tool 1,
                "encoding": optional, what Tool 1 read the document as,
                "sampled_record_indices": optional, original line numbers for
                    each line in raw_sample (from Tool 1), used to mark the
                    head/tail boundary in the prompt,
            }

        Returns:
            JSON string with looker_spec, metadata_report, and rejection info
        """
        try:
            prepared = self._prepare(inputs)
            if isinstance(prepared, str):
                return prepared  # an error or rejection, already JSON-encoded
            guid, lines, prompt = prepared

            reply = self._call_llm(prompt)
            return self._handle_reply(guid, reply, lines, inputs)

        except Exception as e:
            logger.error(f"Structural inspection error: {e}")
            return json.dumps({"status": "error", "error": str(e)})

    async def acall(self, inputs: Dict[str, Any]) -> str:
        """Async twin of __call__, for the per-sheet fan-out in
        extraction/core/pipeline_agent.py. Same contract, same response
        shape - only the LLM call itself runs on the event loop instead of
        blocking a thread.
        """
        try:
            prepared = self._prepare(inputs)
            if isinstance(prepared, str):
                return prepared
            guid, lines, prompt = prepared

            reply = await self._acall_llm(prompt)
            return self._handle_reply(guid, reply, lines, inputs)

        except Exception as e:
            logger.error(f"Structural inspection error: {e}")
            return json.dumps({"status": "error", "error": str(e)})

    def _prepare(self, inputs: Dict[str, Any]):
        """Validate input and build the prompt.

        Returns:
            (guid, lines, prompt) on success, or an already-JSON-encoded
            error/rejection string - shared by __call__ and acall so neither
            duplicates input validation or prompt building.
        """
        guid = inputs.get("guid", "unknown")
        raw_sample = inputs.get("raw_sample", "")
        if not raw_sample:
            return json.dumps({"status": "error", "error": "raw_sample is required"})

        lines = [l for l in raw_sample.split("\n") if l.strip()]
        if not lines:
            return self._reject(guid, REJECT_NO_DATA, "Sample contained no records")

        prompt = self._build_prompt(lines, inputs.get("sampled_record_indices"))
        return guid, lines, prompt

    def _handle_reply(
        self, guid: str, reply: Optional[str], lines: List[str], inputs: Dict[str, Any],
    ) -> str:
        """Turn the model's raw reply into this tool's response JSON."""
        if reply is None:
            return json.dumps({
                "status": "error", "guid": guid,
                "error": "Structural inspection failed: no response from the model",
            })

        try:
            spec = json.loads(reply)
        except json.JSONDecodeError as exc:
            return json.dumps({
                "status": "error", "guid": guid,
                "error": f"Model reply was not valid JSON: {exc}",
            })

        if spec.get("layout_type") == "no_data":
            return self._reject(guid, REJECT_NO_DATA, "Model found no data rows")
        if spec.get("layout_type") == "prose_or_report":
            return self._reject(
                guid, REJECT_NOT_TABULAR,
                "Model classified this as prose or a printed report, not a table",
            )

        report = self._to_metadata_report(spec, lines, inputs)

        return json.dumps({
            "status": "success",
            "guid": guid,
            "rejected": False,
            "rejection_code": None,
            "rejection_reason": None,
            "looker_spec": spec,
            "metadata_report": report,
            "error": None,
        }, indent=2)

    def _reject(self, guid: str, code: str, reason: str) -> str:
        logger.info(f"Rejected {guid}: {reason}")
        return json.dumps({
            "status": "success",
            "guid": guid,
            "rejected": True,
            "rejection_code": code,
            "rejection_reason": reason,
            "looker_spec": None,
            "metadata_report": {"header_field_count": 0, "header_names": [], "data_row_count": 0},
            "error": None,
        })

    def _build_prompt(self, lines: List[str], indices: Optional[List[int]]) -> str:
        """Head+tail slice, with the boundary marked when line numbers are known."""
        gap_at = None
        if indices and len(indices) == len(lines):
            for i in range(1, len(indices)):
                if indices[i] - indices[i - 1] > 1:
                    gap_at = i
                    break

        if gap_at is not None:
            head_block = "\n".join(lines[:gap_at])
            tail_block = "\n".join(lines[gap_at:])
            body = (
                f"--- HEAD (first {gap_at} lines) ---\n{head_block}\n"
                f"--- TAIL (last {len(lines) - gap_at} lines) ---\n{tail_block}"
            )
        else:
            body = "\n".join(lines)

        return f"""You inspect the structure of a flattened document (a
delimited file or spreadsheet export, possibly with a title block above the
header and a footer below the data). You are shown its head and tail only,
not the middle.

Report:
- head_bounds: whether there is a header row, which line it is on (0-indexed
  within what you are shown), which line the data starts on, and the column
  names you can infer.
- tail_bounds: whether there is a footer (totals, page markers, a
  confidentiality notice) below the data, how many lines from the bottom it
  starts, any regex patterns that identify footer lines, and the strategy to
  remove it.
- format_spec: the delimiter, a regex that matches it, the quote character (or
  null if none is used), and the tokens this document uses to mean "no value"
  (e.g. "N/A", "-", "NULL") beyond a plain empty string.
- data_anomalies: whether any row looks like it continues onto the next
  physical line, and whether inline subtotal/summary rows are mixed into the
  data.

If the sample is prose or a printed report rather than a table, set
layout_type to "prose_or_report". If there are no data rows at all, set it to
"no_data".

DOCUMENT:
{body}
"""

    def _call_llm(self, prompt: str) -> Optional[str]:
        for attempt in range(self.max_retries):
            try:
                return self.client.chat(
                    [{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=self.MAX_TOKENS,
                    json_schema=RESPONSE_SCHEMA,
                )
            except (TimeoutError, RuntimeError) as e:
                logger.warning(f"LLM call failed (attempt {attempt+1}/{self.max_retries}): {e}")
                if attempt >= self.max_retries - 1:
                    return None
        return None

    async def _acall_llm(self, prompt: str) -> Optional[str]:
        """Async twin of _call_llm, via LocalLLMClient.achat()."""
        for attempt in range(self.max_retries):
            try:
                return await self.client.achat(
                    [{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=self.MAX_TOKENS,
                    json_schema=RESPONSE_SCHEMA,
                )
            except (TimeoutError, RuntimeError) as e:
                logger.warning(f"LLM call failed (attempt {attempt+1}/{self.max_retries}): {e}")
                if attempt >= self.max_retries - 1:
                    return None
        return None

    def _to_metadata_report(
        self, spec: Dict[str, Any], lines: List[str], inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Derive the flat report shape Tool 3/Tool 4 already read.

        Width bookkeeping (modal/min/max field count, raggedness) is measured
        here, mechanically, against the already-identified delimiter, rather
        than asked of the model - it's arithmetic on the sample, not a
        judgement call.
        """
        head = spec.get("head_bounds") or {}
        tail = spec.get("tail_bounds") or {}
        fmt = spec.get("format_spec") or {}

        delimiter_type = fmt.get("delimiter_type") or "comma"
        delimiter = _DELIMITER_CHAR_BY_TYPE.get(delimiter_type, ",")
        quote_char = fmt.get("quote_char")

        has_header = bool(head.get("has_header"))
        header_names = list(head.get("inferred_columns") or [])
        header_row_index = head.get("header_line_index", 0) if has_header else -1
        header_source = "row" if has_header else "positional"

        data_start = head.get("data_start_line_index")
        footer_lines = max(0, int(tail.get("footer_start_from_bottom") or 0))
        data_end = len(lines) - footer_lines if footer_lines else len(lines)
        data_lines = lines[data_start:data_end] if data_start is not None else lines[:data_end]

        widths = [len(self._split(line, delimiter, quote_char)) for line in data_lines] or [0]
        modal_field_count = Counter(widths).most_common(1)[0][0]

        if not header_names:
            header_names = [f"column_{i}" for i in range(modal_field_count)]

        return {
            "delimiter": delimiter,
            "delimiter_name": delimiter_type,
            "format": delimiter_type,
            "quote_char": quote_char,
            "encoding": inputs.get("encoding") or "utf-8",
            "header_row_index": header_row_index,
            "header_source": header_source,
            "header_field_count": len(header_names),
            "header_names": header_names,
            "sheet_record_count": inputs.get("total_records"),
            "sheet_byte_length": inputs.get("total_bytes"),
            "sheet_names": inputs.get("sheet_names") or [],
            "data_row_count": len(data_lines),
            "modal_field_count": modal_field_count,
            "min_field_count": min(widths),
            "max_field_count": max(widths),
            "ragged": len(set(widths)) > 1,
            # New, additive fields consumed by Tool 3 (null_values) and
            # Tool 4 (footer_start_from_bottom) - see their own docstrings.
            "footer_start_from_bottom": footer_lines,
            "null_values": fmt.get("null_values") or [],
        }

    @staticmethod
    def _split(line: str, delimiter: str, quote_char: Optional[str]) -> List[str]:
        import csv
        try:
            return next(csv.reader([line], delimiter=delimiter, quotechar=quote_char or '"'))
        except (csv.Error, StopIteration):
            return line.split(delimiter)
