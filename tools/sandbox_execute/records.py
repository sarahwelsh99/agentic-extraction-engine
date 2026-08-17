"""Record splitting for glean's flattened workbook text.

glean stores spreadsheets in body_text by flattening the workbook, and the
flattening has to be reversed before anything else makes sense:

  * a row ends at a blank line, not at every newline
  * a row ending in SHEET_MARKER names a worksheet rather than holding data
  * CELL_NEWLINE is a line break inside a single cell
  * ZERO_WIDTH characters are invisible litter that str.strip() will not remove

This lives in one place because the tool that profiles a document and the
sandbox that runs extraction code against it must split rows identically.
When they disagreed, the column indices worked out by profiling addressed
different cells than the ones handed to the generated code, and every blank
line became a phantom row that reported all fields missing.
"""

from typing import List, Tuple

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
            sheets.append(row[: -len(SHEET_MARKER)])
            continue
        # Keep the cell intact: a break inside a cell is not a record boundary.
        # Zero-width characters go before the strip, since strip() leaves them.
        row = strip_zero_width(row.replace(CELL_NEWLINE, " ")).strip()
        if row:
            records.append(row)

    return records, sheets
