"""Tool 2: delimiter detector for flattened worksheet text.

Scans the sample from Tool 1 and reports how the document is laid out:
which delimiter separates fields, where the header is and how wide it is, and
how large the sheet is. That metadata report is what Tool 3 uses to generate an
extraction script.

This tool makes no judgement about what the columns mean. Documents reaching it
have already passed an upstream relevancy check, so the question is not whether
a document is worth extracting but how to read it.
"""
import csv
import logging
import re
from collections import Counter
from typing import Any, Dict, List, Optional

from tools.base import AgentTool, ToolResponse
from extraction.core.records import strip_zero_width

logger = logging.getLogger(__name__)


class DelimiterDetectorTool(AgentTool):
    """Detect the delimiter and describe the shape of a document."""

    # Candidate delimiters, in the order they win ties
    DELIMITERS = (
        (",", "comma"),
        ("|", "pipe"),
        ("\t", "tab"),
        (";", "semicolon"),
    )
    FORMAT_BY_DELIMITER = {",": "csv", "|": "pipe", "\t": "tab", ";": "semicolon"}

    # Structural reasons a document cannot be described
    # No NO_HEADER_ROW code: a document without a header is numbered
    # positionally rather than refused (see execute).
    REJECT_NO_DATA = "NO_DATA_ROWS"
    REJECT_NO_DELIMITER = "NO_DELIMITER_FOUND"
    REJECT_SINGLE_COLUMN = "SINGLE_COLUMN"

    @property
    def name(self) -> str:
        return "delimiter_detector"

    @property
    def description(self) -> str:
        return (
            "Detect the delimiter and report document structure (header position "
            "and width, sheet size) for script generation"
        )

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "raw_sample": {
                    "type": "string",
                    "description": "Sampled records from Tool 1, newline separated",
                },
                "detected_format_hint": {
                    "type": "string",
                    "description": "Format hint from Tool 1. Only a hint: the delimiter is detected here",
                    "enum": ["csv", "json", "pipe", "tab", "space_delimited", "semicolon", "unknown"],
                },
                "actual_header_row_index": {
                    "type": "integer",
                    "description": "Header row hint from Tool 1 (0-indexed)",
                    "default": 0,
                },
                "sheet_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Worksheet names from Tool 1; a header is sometimes held here",
                    "default": [],
                },
                "total_records": {
                    "type": ["integer", "null"],
                    "description": "Record count of the whole document from Tool 1, for sheet size",
                    "default": None,
                },
                "total_bytes": {
                    "type": ["integer", "null"],
                    "description": "Byte size of the whole document from Tool 1",
                    "default": None,
                },
                "encoding": {
                    "type": "string",
                    "description": "What Tool 1 read the document as; reported, not detected",
                    "default": "utf-8",
                },
                "guid": {
                    "type": ["string", "null"],
                    "description": "Document GUID for tracking",
                    "default": None,
                },
            },
            "required": ["raw_sample"],
        }

    @property
    def output_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["success", "error"]},
                "guid": {"type": ["string", "null"]},
                "rejected": {"type": "boolean"},
                "rejection_code": {"type": ["string", "null"]},
                "rejection_reason": {"type": ["string", "null"]},
                "metadata_report": {
                    "type": "object",
                    "properties": {
                        "delimiter": {"type": "string"},
                        "delimiter_name": {"type": "string"},
                        "format": {"type": "string"},
                        "quote_char": {"type": ["string", "null"]},
                        "quoted_field_rows": {"type": "integer"},
                        "has_non_ascii": {"type": "boolean"},
                        "header_row_index": {"type": "integer"},
                        "header_source": {"type": "string"},
                        "header_field_count": {"type": "integer"},
                        "header_char_length": {"type": "integer"},
                        "header_names": {"type": "array", "items": {"type": "string"}},
                        "sheet_record_count": {"type": ["integer", "null"]},
                        "sheet_byte_length": {"type": ["integer", "null"]},
                        "sampled_record_count": {"type": "integer"},
                        "data_row_count": {"type": "integer"},
                        "modal_field_count": {"type": "integer"},
                        "min_field_count": {"type": "integer"},
                        "max_field_count": {"type": "integer"},
                        "ragged": {"type": "boolean"},
                        "sheet_names": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "error": {"type": ["string", "null"]},
                "timestamp": {"type": "string"},
            },
            "required": ["status", "error", "timestamp"],
        }

    def execute(self, input_data: Dict[str, Any]) -> ToolResponse:
        """Produce the metadata report for one document."""
        try:
            raw_sample = input_data.get("raw_sample", "")
            guid = input_data.get("guid")
            sheet_names = input_data.get("sheet_names") or []
            hint_index = input_data.get("actual_header_row_index", 0)

            if not raw_sample:
                return ToolResponse(status="error", error="raw_sample is required")

            # Cleaned again here, not only in Tool 1: this tool is given a
            # sample rather than a document, and a byte-order mark left in it
            # would end up inside a column name.
            records = [
                line for line in strip_zero_width(raw_sample).split("\n")
                if line.strip()
            ]
            if not records:
                return self._reject(guid, self.REJECT_NO_DATA, "Sample contained no records")

            # Step 1: the delimiter, decided by which one splits most consistently
            delimiter, delimiter_name, confidence = self._detect_delimiter(records)
            if delimiter is None:
                return self._reject(
                    guid, self.REJECT_NO_DELIMITER,
                    "No delimiter splits the sample into consistent fields",
                )

            quote_char, quoted_rows = self._detect_quote_char(records, delimiter)
            rows = self._parse_rows(records, delimiter, quote_char)

            # Step 2: where the header is and how wide
            headers, data_rows, header_index, header_source = self._locate_header(
                rows, sheet_names, hint_index, delimiter
            )

            # No header is not a reason to give up. The rows are still readable
            # and the delimiter is known; only the column names are missing, so
            # the columns are numbered instead. Naming a column after whatever
            # sat in row 0 would be worse: on real documents that made an
            # employee's name the label for a column.
            if header_source == "none" or not headers:
                widths = [len(r) for r in rows]
                field_count = Counter(widths).most_common(1)[0][0]
                headers = [f"column_{i}" for i in range(field_count)]
                header_index = -1  # nothing is skipped as a header
                header_source = "positional"

            # Describe every row below the header, not only those matching the
            # table's modal width: raggedness is a property of the document and
            # the script generator needs to know about it.
            all_data = (
                rows[header_index + 1:] if header_source == "row" else rows
            )
            if not all_data:
                return self._reject(
                    guid, self.REJECT_NO_DATA,
                    f"Header found ({len(headers)} fields) but no data rows "
                    f"beneath it; there is nothing to extract",
                    delimiter=delimiter, delimiter_name=delimiter_name,
                )

            widths = [len(r) for r in all_data]
            modal_field_count = self._table_width(widths)

            # A table has at least two columns. When MOST rows hold a single
            # field the document is prose or a printed report — a title, a date
            # range, an address — and extracting it yields one column of whole
            # lines, which is not structured data however cleanly it runs.
            #
            # Counted over the raw widths, not the chosen table width: that is
            # picked from rows of two fields or more, so it can never be 1 and
            # would never trip this.
            #
            # Judged on width rather than on delimiter confidence: legitimately
            # ragged tables score low confidence too (a 187-column sheet here
            # scored 0.167), so confidence would reject real tables.
            single_field_rows = sum(1 for w in widths if w < self.MIN_COLUMNS)
            if single_field_rows > len(widths) * self.MAX_SINGLE_FIELD_SHARE:
                return self._reject(
                    guid, self.REJECT_SINGLE_COLUMN,
                    f"{single_field_rows} of {len(widths)} rows hold a single "
                    f"field; this is prose or a printed report, not a table",
                    delimiter=delimiter, delimiter_name=delimiter_name,
                )

            # Names must be unique, and every position must have one. Callers
            # key rows by name, so two columns sharing a name collapse into one
            # and the data in the later column is lost. Blank header cells are
            # common (merged cells, spacer columns) and repeated ones almost as
            # common (a roster with THU, FRI, SAT repeating weekly).
            headers = self._unique_names(headers)

            header_line = (
                records[header_index] if 0 <= header_index < len(records)
                else ""     # header came from a sheet name or was numbered
            )

            report = {
                "delimiter": delimiter,
                "delimiter_name": delimiter_name,
                "delimiter_confidence": round(confidence, 3),
                "format": self.FORMAT_BY_DELIMITER.get(delimiter, "unknown"),
                # Detected, not assumed: a document with no quoting reports
                # none rather than claiming a character it never uses.
                "quote_char": quote_char,
                "quoted_field_rows": quoted_rows,
                # Not detectable here and not claimed as such: body_text arrives
                # already decoded, so this is what Tool 1 read it as.
                "encoding": input_data.get("encoding") or "utf-8",
                "has_non_ascii": any(ord(c) > 127 for c in raw_sample),

                "header_row_index": header_index,
                "header_source": header_source,
                "header_field_count": len(headers),
                "header_char_length": len(header_line),
                "header_names": headers,
                "header_cells_that_look_like_data": self._leaked_header_cells(
                    headers, all_data
                ),

                # Whole-document size, from Tool 1; the sample is only a window
                "sheet_record_count": input_data.get("total_records"),
                "sheet_byte_length": input_data.get("total_bytes"),
                "sheet_names": sheet_names,

                # What the sample itself showed
                "sampled_record_count": len(records),
                "data_row_count": len(all_data),
                "table_row_count": len(data_rows),
                "modal_field_count": modal_field_count,
                # The chosen width against what the header declares. A large gap
                # means the rows profiled are not the table the header describes,
                # so its names will be applied to the wrong values.
                "header_matches_data": (
                    modal_field_count >= len(headers) * self.MIN_HEADER_COVERAGE
                    if header_source == "row" else True
                ),
                "min_field_count": min(widths),
                "max_field_count": max(widths),
                "ragged": len(set(widths)) > 1,
            }

            return ToolResponse(
                status="success",
                guid=guid,
                rejected=False,
                rejection_code=None,
                rejection_reason=None,
                metadata_report=report,
                error=None,
            )

        except Exception as e:
            logger.error(f"Structure detection error: {str(e)}")
            return ToolResponse(status="error", error=str(e))

    def _reject(
        self,
        guid: Optional[str],
        code: str,
        reason: str,
        delimiter: str = "",
        delimiter_name: str = "unknown",
    ) -> ToolResponse:
        """Mark this guid as not describable, with the reason recorded.

        A successful tool run: the tool did its job and the answer is that the
        document has no structure to report.
        """
        logger.info(f"Rejected {guid}: {reason}")
        return ToolResponse(
            status="success",
            guid=guid,
            rejected=True,
            rejection_code=code,
            rejection_reason=reason,
            metadata_report={
                "delimiter": delimiter,
                "delimiter_name": delimiter_name,
                "format": self.FORMAT_BY_DELIMITER.get(delimiter, "unknown"),
                "header_field_count": 0,
                "header_names": [],
                "data_row_count": 0,
            },
            error=None,
        )

    def _detect_delimiter(self, records: List[str]) -> tuple[Optional[str], str, float]:
        """Pick the delimiter that splits the sample most consistently.

        Counting occurrences is not enough: prose cells are full of commas and
        spaces. What identifies a delimiter is that it yields the *same* number
        of fields on row after row, so each candidate is scored by the share of
        records agreeing on a field count above one.

        Returns:
            Tuple of (delimiter, name, confidence), delimiter None if none fits
        """
        sample = records[:50]
        best = (None, "unknown", 0.0)

        for delimiter, name in self.DELIMITERS:
            widths = [len(row) for row in self._parse_rows(sample, delimiter)]
            multi = [w for w in widths if w > 1]
            if not multi:
                continue

            modal, agreeing = Counter(multi).most_common(1)[0]
            # Agreement across the sample, weighted so a 2-field split does not
            # beat a consistent 40-field one on ties
            confidence = (agreeing / len(widths)) * min(1.0, 0.5 + modal / 20)

            if confidence > best[2]:
                best = (delimiter, name, confidence)

        if best[0] is None:
            return None, "unknown", 0.0
        return best

    # Quote characters worth trying. Anything else is vanishingly rare in
    # spreadsheet exports and would cost more in false positives than it earns.
    QUOTE_CANDIDATES = ('"', "\'")

    def _detect_quote_char(
        self, records: List[str], delimiter: str
    ) -> tuple[Optional[str], int]:
        """Find the quote character, by seeing which one actually does work.

        A candidate is judged by whether honouring it changes the row at all,
        against a naive split on the delimiter. Two things count: fewer fields,
        because a delimiter was protected inside one; and an unchanged field
        count with different values, because surrounding quotes were removed.
        Only counting the first missed documents that quote every field but
        never happen to enclose a delimiter.

        A document that quotes nothing reports None, rather than claiming a
        character it never uses.

        Returns:
            Tuple of (quote character or None, rows where it mattered)
        """
        sample = records[:20]
        best_char, best_rows = None, 0

        for candidate in self.QUOTE_CANDIDATES:
            effective = 0
            for record in sample:
                if candidate not in record:
                    continue
                naive = [f.strip() for f in record.split(delimiter)]
                try:
                    honoured = [f.strip() for f in next(csv.reader(
                        [record], delimiter=delimiter, quotechar=candidate))]
                except (csv.Error, StopIteration):
                    continue
                if honoured != naive:
                    effective += 1
            if effective > best_rows:
                best_char, best_rows = candidate, effective

        return best_char, best_rows

    @staticmethod
    def _parse_rows(
        records: List[str], delimiter: str, quote_char: Optional[str] = None
    ) -> List[List[str]]:
        """Split records into fields, honouring quoted values."""
        rows = []
        for record in records:
            try:
                fields = next(csv.reader(
                    [record], delimiter=delimiter, quotechar=quote_char or '"'))
            except (csv.Error, StopIteration):
                fields = record.split(delimiter)
            rows.append([f.strip() for f in fields])
        return rows

    def _locate_header(
        self,
        rows: List[List[str]],
        sheet_names: List[str],
        hint_index: int,
        delimiter: str,
    ) -> tuple[List[str], List[List[str]], int, str]:
        """Find the header row structurally.

        The header is the first row as wide as the table itself, so the modal
        field count across the sample identifies it, and it has to read like
        labels rather than values. Some worksheets keep the header in the sheet
        name instead, with every row being data.

        When neither yields a header that is said plainly, rather than assuming
        row 0: assuming it names columns after whatever data sat in row 0.

        Returns:
            Tuple of (headers, data_rows, header_row_index, source) where source
            is "row", "sheet_name" or "none"
        """
        widths = [len(r) for r in rows]
        table_widths = [w for w in widths if w >= 2]

        if not table_widths:
            index = hint_index if hint_index < len(rows) else 0
            return (rows[index] if rows else []), rows[index + 1:], index, "none"

        modal_width = self._table_width(table_widths)

        # A header sits at the top, above its data, so only the opening rows are
        # candidates. Searching the whole sheet found "headers" near the bottom
        # with too few rows beneath to judge them by. The search is not limited
        # to rows of table width: a header is routinely a field or two wider
        # than its data, whose trailing empty cells were trimmed.
        for index, row in enumerate(rows[: self.HEADER_SEARCH_DEPTH]):
            below = rows[index + 1:]
            if not below or len(row) < 2:
                continue

            # Reading like labels is not enough: a payroll row of place names
            # and people reads like labels too. What separates a header from a
            # data row is that it does not resemble the column beneath it.
            if self._row_is_data(row, below):
                break  # the data has begun, so no header precedes it

            if self._looks_like_header(row):
                return row, below, index, "row"
            # Otherwise a title or a spacer: keep looking beneath it

        for name in sheet_names:
            fields = [f.strip() for f in next(csv.reader([name], delimiter=delimiter), [])]
            if len(fields) == modal_width and self._looks_like_header(fields):
                data = [r for r in rows if len(r) == modal_width]
                return fields, data, -1, "sheet_name"  # -1: from the sheet name

        index = hint_index if hint_index < len(rows) else 0
        return rows[index], rows[index + 1:], index, "none"

    @staticmethod
    def _unique_names(headers: List[str]) -> List[str]:
        """Give every column a distinct, non-empty name.

        A blank cell becomes column_<index>; a repeat gains a numeric suffix.
        Real names are left alone, so a document with a good header reads exactly
        as it did before.

        Returns:
            Names of the same length and order as the input
        """
        seen: Dict[str, int] = {}
        named = []
        for index, header in enumerate(headers):
            name = (header or "").strip() or f"column_{index}"
            if name in seen:
                seen[name] += 1
                name = f"{name}_{seen[name]}"
            else:
                seen[name] = 1
            named.append(name)
        return named

    @staticmethod
    def _table_width(widths: List[int]) -> int:
        """The width of the table these rows describe.

        Scored by the cells each width accounts for, not by how many rows have
        it. A plain mode picks the wrong shape whenever a sheet carries a tidy
        narrow tail beneath ragged wide data: on a staff roster whose data rows
        were 20-30 fields wide and whose summary rows were all 4, the mode chose
        4, every row was truncated to four values, and the header's first four
        names were applied to whatever happened to land there.

        Ragged wide rows rarely repeat a width, so they never form a mode;
        weighting by cells lets them win anyway.
        """
        usable = [w for w in widths if w >= 2] or widths
        if not usable:
            return 0
        counts = Counter(usable)
        return max(
            (w for w, n in counts.items() if n >= 2),
            key=lambda w: w * counts[w],
            default=counts.most_common(1)[0][0],
        )

    @staticmethod
    def _looks_like_header(row: List[str]) -> bool:
        """Distinguish a row of labels from a row of values.

        Judged by proportion, not by any single cell: a wide sheet legitimately
        carries a few enormous headers among hundreds of ordinary ones, and
        rejecting the row for one odd cell throws out real headers.
        """
        cells = [c.strip() for c in row if c and c.strip()]
        if len(cells) < max(2, len(row) * 0.5):
            return False

        total = len(cells)
        numeric = sum(1 for c in cells if re.fullmatch(r"[-+]?[\d.,/%:]+", c))
        addresses = sum(1 for c in cells if "@" in c)
        prose = sum(1 for c in cells if len(c) > 128)

        # A single odd cell must not condemn the row: on a three-column header
        # one leaked value is 33%, and the right response is to flag that cell,
        # not to reject the document. Numeric stays strictly proportional, since
        # a mostly-numeric row is the real signal that this is data.
        return (
            numeric <= total * 0.3
            and addresses <= max(1, total * 0.3)
            and prose <= max(1, total * 0.5)
        )

    # Shapes distinctive enough that a header cell sharing one with its own
    # column is a leaked data value rather than a label. Plain words are
    # excluded: 'Name' over a column of names is a real header.
    _SHAPES = (
        ("uuid", re.compile(r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$", re.I)),
        ("email", re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)),
        ("datetime", re.compile(r"^\d{1,4}[/-]\d{1,2}[/-]\d{1,4}[ T]\d{1,2}:\d{2}")),
        ("date", re.compile(r"^\d{1,4}[/-]\d{1,2}[/-]\d{1,4}$")),
        ("time", re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")),
        ("number", re.compile(r"^[-+]?[\d,]+(\.\d+)?$")),
        ("comma_name", re.compile(r"^[A-Z][\w'-]+,\s+[A-Z][\w'-]+$")),
        ("hex_id", re.compile(r"^[0-9a-f]{16,}$", re.I)),
    )

    # A candidate needs this many distinctively-typed columns before its
    # resemblance to the data means anything, and this share of them matching
    # before it is judged a data row. Measured on the corpus: genuine headers
    # score 0%, data rows posing as headers score 75-100%, nothing lands between
    # 0% and 56%, so the threshold sits in a wide empty gap.
    MIN_DISTINCTIVE_COLUMNS = 2
    # Rows from the top searched for a header; a title block can sit above it
    HEADER_SEARCH_DEPTH = 5

    # Fewest columns that make a table. Below this the document is prose.
    MIN_COLUMNS = 2

    # Share of the header's width the profiled rows must reach before its names
    # are trusted. Data rows are routinely a little narrower than the header
    # (trailing empty cells are trimmed), so this is deliberately lenient.
    MIN_HEADER_COVERAGE = 0.5

    # Share of rows allowed to hold a single field before the document is judged
    # not to be a table at all.
    MAX_SINGLE_FIELD_SHARE = 0.5
    DATA_ROW_MATCH_RATIO = 0.7

    def _row_is_data(self, candidate: List[str], below: List[List[str]]) -> bool:
        """True when a candidate header is really one more row of the data.

        Compares it column by column against what lies underneath. Only columns
        whose values have a distinctive shape count — a number, a date, an email,
        an id. A real header puts a word above those; a data row puts another
        number, date or email.

        Plain-word columns are ignored deliberately: 'name' above a column of
        names is indistinguishable from 'Adam' above one, so they carry no
        evidence either way.
        """
        distinctive = matched = 0

        for index, header_cell in enumerate(candidate):
            column = [
                row[index] for row in below[:8]
                if index < len(row) and row[index].strip()
            ]
            if not column:
                continue

            shapes = [self._shape_of(v) for v in column]
            modal_shape, count = Counter(shapes).most_common(1)[0]
            if modal_shape is None or count < max(1, len(column) * 0.6):
                continue  # column has no distinctive shape; no evidence here

            distinctive += 1
            if self._shape_of(header_cell) == modal_shape:
                matched += 1

        if distinctive < self.MIN_DISTINCTIVE_COLUMNS:
            return False

        return matched / distinctive >= self.DATA_ROW_MATCH_RATIO

    @classmethod
    def _shape_of(cls, value: str) -> Optional[str]:
        """Classify a value into a distinctive shape, or None for plain text."""
        text = (value or "").strip()
        for name, pattern in cls._SHAPES:
            if pattern.match(text):
                return name
        return None

    def _leaked_header_cells(
        self, headers: List[str], data_rows: List[List[str]]
    ) -> List[int]:
        """Header positions whose cell looks like one more value of its column.

        Whole-row detection passes on rows that are mostly labels, but a single
        cell can still be a leaked value where the export met a blank or merged
        header. Reported so the generated script can treat that column's name
        as unreliable.

        Returns:
            Column indexes whose header is really data
        """
        leaked = []
        for index, header in enumerate(headers):
            shape = self._shape_of(header)
            if shape is None:
                continue
            column = [
                row[index] for row in data_rows[:8]
                if index < len(row) and row[index].strip()
            ]
            if not column:
                continue
            matching = sum(1 for v in column if self._shape_of(v) == shape)
            if matching >= max(1, len(column) * 0.6):
                leaked.append(index)
        return leaked
