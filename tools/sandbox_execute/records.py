"""Record splitting for glean's flattened workbook text.

glean stores spreadsheets in body_text by flattening the workbook, and the
flattening has to be reversed before anything else makes sense:

  * a row ends at a blank line, not at every newline
  * a row ending in SHEET_MARKER usually names a worksheet - but glean also
    marks ordinary table rows, so a marked row that looks like one is data
    (see _looks_like_table_row)
  * CELL_NEWLINE is a line break inside a single cell
  * ZERO_WIDTH characters are invisible litter that str.strip() will not remove

This lives in one place because the tool that profiles a document and the
sandbox that runs extraction code against it must split rows identically.
When they disagreed, the column indices worked out by profiling addressed
different cells than the ones handed to the generated code, and every blank
line became a phantom row that reported all fields missing.
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

# U+2000 EN QUAD, then SOH, then NUL. Built from code points on purpose: the
# leading character is not an ASCII space, and it does not survive copy/paste
# reliably. A plain space here silently matches nothing.
SHEET_MARKER = " \x01\x00"
CELL_NEWLINE = "\x04"
ROW_SEPARATOR = "\n\n"

assert [ord(c) for c in SHEET_MARKER] == [0x2000, 0x01, 0x00], (
    "SHEET_MARKER corrupted: expected U+2000 SOH NUL"
)

# Zero-width characters glean leaves in the text. str.strip() does NOT remove
# these — it strips only what Python calls whitespace — so a byte-order mark at
# the start of a header cell survives into the column name as an invisible
# character that nothing downstream matches. Measured over 2% of the corpus,
# U+FEFF appears 7,019 times: on the order of 350,000 times overall.
ZERO_WIDTH = "﻿​‌‍⁠"
_ZERO_WIDTH_MAP = {ord(c): None for c in ZERO_WIDTH}


def strip_zero_width(text: str) -> str:
    """Remove the invisible characters str.strip() leaves behind.

    Exposed separately because any stage that receives text rather than a whole
    document — the structure detector works from a sample, not from body_text —
    needs the same cleaning, or a byte-order mark reaches a column name.
    """
    return (text or "").translate(_ZERO_WIDTH_MAP)


# Excel caps a worksheet name at 31 characters, so a longer marked row cannot
# be an Excel tab name.
MAX_TAB_NAME_LENGTH = 31


def _looks_like_table_row(text: str) -> bool:
    """Whether a SHEET_MARKER-terminated row is a row of a table rather than a
    worksheet name.

    glean puts SHEET_MARKER on more than tab names. A Google Sheet's first row
    often carries it with no tab name before it at all, and an email marks the
    first row of each table pasted into its body. Treated as a name, that row -
    usually the table's header - was dropped from the data: 4,370 of a 12,690
    document sample (34%) lost their header this way, and the column names
    became the first data record instead (e.g. guid
    3abecca0-30ec-3fb9-e9f1-16d0759f0bb0, whose real "CMP Case ID, Link to
    ACC, ..." header became a sheet name).

    Three or more non-empty cells is a table row. Two cells is only a table
    row past MAX_TAB_NAME_LENGTH, because real tab names do contain a comma
    ("SumIF, SumIFs&AverageIF", "Alviola,Jess Basil" were both seen in
    production) and must stay names. The cost of that line is that a short
    two-column header that is the first, marked row of a document is still
    read as a name, as before.
    """
    non_empty = [c for c in text.split(",") if c.strip()]
    if len(non_empty) >= 3:
        return True
    return len(non_empty) >= 2 and len(text.strip()) > MAX_TAB_NAME_LENGTH


def split_records(body_text: str) -> Tuple[List[str], List[str]]:
    """Split flattened workbook text into records and worksheet names.

    Falls back to plain newline splitting for sources that are ordinary text
    rather than flattened workbooks.

    Args:
        body_text: Raw document text

    Returns:
        Tuple of (records, sheet_names)
    """
    if not body_text:
        return [], []

    rows = (
        body_text.split(ROW_SEPARATOR)
        if ROW_SEPARATOR in body_text
        else body_text.split("\n")
    )

    records: List[str] = []
    sheets: List[str] = []

    for row in rows:
        if row.endswith(SHEET_MARKER):
            row = row[: -len(SHEET_MARKER)]
            if not _looks_like_table_row(row):
                sheets.append(row)
                continue
            # A marked table row is data. Dropping it here is what left a
            # document whose rows are nearly all marked with no records at all,
            # and Tool 2 then failed with "raw_sample is required".
        # Keep the cell intact: a break inside a cell is not a record boundary.
        # Zero-width characters go before the strip, since strip() leaves them.
        row = strip_zero_width(row.replace(CELL_NEWLINE, " ")).strip()
        if row:
            records.append(row)

    return records, sheets


@dataclass
class SheetBlock:
    """One worksheet's own slice of a flattened multi-tab document.

    `body_text` is already a valid flattened snippet (rows joined by
    ROW_SEPARATOR) scoped to this sheet alone, so it can be handed straight
    back into fetch_and_sample/structural_inspector/sandbox_execute exactly
    as if it were the whole document - none of those need to know sheets
    exist at all.
    """
    name: Optional[str]
    body_text: str


def _looks_like_header_row(text: str) -> bool:
    """A lightweight, delimiter-agnostic signal for "this looks like a real
    header row" (several distinct cells) versus "this is just another
    title/tab-name" (one real cell plus trailing empties, or none at all).
    Comma is used as the probe delimiter since every multi-sheet document
    seen in production is comma-delimited; the safe failure mode for a
    document that turns out to use another delimiter is treating a real
    header as another title-like row instead - the Structural Inspector
    still sees it as ordinary content either way, just one line later as
    that sheet's own first content line rather than folded further up.
    """
    non_empty = [c for c in text.split(",") if c.strip()]
    return len(non_empty) >= 2


# A document whose marker-terminated lines dominate its total line count is
# not a real multi-tab workbook - a genuine workbook's tabs hold actual data,
# not wall-to-wall boundaries. Confirmed on a real structured_pending guid (an
# automated test-run log, 707 of 712 lines marker-terminated - 99.3% density)
# that would otherwise fan out into 707 concurrent LLM calls for a document
# that is not a workbook at all. Same failure mode already documented for
# error_dense's 3,373-sheet flattened-PDF incident, just smaller and inside a
# population assumed clean of it. Two real multi-tab workbooks checked
# alongside it sat at 10-12% density, so 90% catches only the pathological
# case rather than anything resembling genuine tab structure.
MAX_MARKER_DENSITY = 0.9


def _looks_like_a_real_workbook(rows: List[str]) -> bool:
    if not rows:
        return True
    marker_rows = sum(1 for row in rows if row.endswith(SHEET_MARKER))
    return marker_rows / len(rows) <= MAX_MARKER_DENSITY


def split_sheets(body_text: str) -> List[SheetBlock]:
    """Split a flattened document into its worksheets, preserving position.

    split_records() already recognizes SHEET_MARKER-terminated rows, but only
    to collect names into a side list - it discards *where* a sheet's data
    begins and ends, so every sheet's rows land in one flat, unattributed
    list. This walks the same rows but keeps that boundary: a marker row
    starts a new sheet and supplies its name. A document is sometimes
    flattened with a second marker-terminated row right after the name; that
    is only folded into the sheet's own content (as a candidate header line -
    the Structural Inspector resolves whether it's a real header, exactly as
    for a single-table document) when it actually looks like a header - at
    least two non-empty cells. A second marker row that is just another
    title/label (one real cell) is a separate, usually-empty sheet in its own
    right, not this sheet's header: confirmed on a real workbook where
    "Week 1," and "WEEK 1 PGU," sat back to back - folding the second into
    the first attributed real mentor/agent data to the wrong, empty tab and
    swallowed the actual header line ("Mentor,Agent,Highlights,...") as
    ordinary content instead.

    A marked row that looks like a table row (_looks_like_table_row) is
    content wherever it sits, never a name. That covers the layouts where the
    marker lands on data rather than on a tab name: a Google Sheet whose
    header is its first, marked row with no name before it, an email whose
    pasted tables each have a marked first row, and an export that marks
    every data row. Such a row stays in the block it appears in rather than
    starting a new one, so the sheet count is the number of real names and
    no extra unnamed blocks are created: write_parquet_to_gcs names an
    unnamed block's file <guid>.parquet, so two of them in one document would
    write to the same path.

    A sheet's own last line is genuinely its own tail (not a slice of the
    whole document's end), which matters for the Micro-Slicer's tail window.

    Returns:
        One SheetBlock per detected worksheet, in document order. A document
        with no SHEET_MARKER rows at all (an ordinary, non-workbook source),
        or one so marker-dense it fails _looks_like_a_real_workbook's sanity
        check, returns a single SheetBlock(name=None, body_text=<original
        text>), so callers never need to special-case "not actually
        multi-sheet" or "not actually a workbook at all".
    """
    if not body_text:
        return [SheetBlock(name=None, body_text=body_text or "")]

    # Fast path: no SHEET_MARKER at all means an ordinary, non-workbook
    # document - the overwhelming majority. Returned completely untouched,
    # rather than reconstructed through the row-by-row loop below, which
    # both matches today's behavior exactly and costs nothing extra.
    if SHEET_MARKER not in body_text:
        return [SheetBlock(name=None, body_text=body_text)]

    rows = (
        body_text.split(ROW_SEPARATOR)
        if ROW_SEPARATOR in body_text
        else body_text.split("\n")
    )

    if not _looks_like_a_real_workbook(rows):
        return [SheetBlock(name=None, body_text=body_text)]

    blocks: List[SheetBlock] = []
    current_name: Optional[str] = None
    current_lines: List[str] = []
    in_marker_run = False

    def flush() -> None:
        if current_lines or current_name is not None:
            blocks.append(SheetBlock(
                name=current_name, body_text=ROW_SEPARATOR.join(current_lines),
            ))

    for row in rows:
        if row.endswith(SHEET_MARKER):
            text = row[: -len(SHEET_MARKER)]
            # A marker row immediately following another one, that itself
            # looks like a real header (>= 2 non-empty cells), is this sheet's
            # content - not a third+ sheet name. So is any marked row that
            # looks like a row of a table, wherever it sits.
            is_content = (
                (in_marker_run and _looks_like_header_row(text))
                or _looks_like_table_row(text)
            )
            if not is_content:
                flush()
                current_name = text
                current_lines = []
                in_marker_run = True
            else:
                cleaned = strip_zero_width(text.replace(CELL_NEWLINE, " ")).strip()
                if cleaned:
                    current_lines.append(cleaned)
            continue

        in_marker_run = False
        cleaned = strip_zero_width(row.replace(CELL_NEWLINE, " ")).strip()
        if cleaned:
            current_lines.append(cleaned)

    flush()

    return blocks or [SheetBlock(name=None, body_text=body_text)]


def has_multiple_sheets(body_text: str) -> bool:
    """True iff the document names more than one worksheet.

    A document with a single sheet marker, or none, is single-sheet: there is
    nothing to fan out per sheet, so it takes the ordinary single-document
    path at no extra cost.
    """
    return len(split_sheets(body_text)) > 1
