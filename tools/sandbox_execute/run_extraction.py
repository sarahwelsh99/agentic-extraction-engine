"""Sandbox entrypoint: run generated extraction code against a document.

Reads a JSON job from stdin and writes a JSON result to stdout. The job carries
the document text plus the layout Tool 2 worked out, because the generated code
addresses cells by index and those indices are only meaningful against the same
row splitting and the same table block that Tool 2 profiled.
"""

import sys
import json
import csv
import io
import os
from typing import Dict, Any, List

from records import split_records

# Read generated code from mounted file or environment variable
if os.path.exists("/app/generated_code.py"):
    with open("/app/generated_code.py", "r") as f:
        generated_code = f.read()
else:
    generated_code = os.environ.get("GENERATED_CODE")

if not generated_code:
    print(json.dumps({
        "status": "error",
        "error": "No generated code provided (file or env var)"
    }))
    sys.exit(1)

raw_stdin = sys.stdin.read()

try:
    job = json.loads(raw_stdin)
except json.JSONDecodeError as exc:
    print(json.dumps({
        "status": "error",
        "error": f"stdin is not a JSON job: {exc}"
    }))
    sys.exit(1)

body_text = job.get("body_text", "")
delimiter = job.get("delimiter", ",")
quote_char = job.get("quote_char") or '"'
# -1 means the header was taken from a worksheet name, so no row is a header
header_row_index = job.get("header_row_index", 0)
min_field_count = job.get("min_field_count") or 0
column_names = job.get("column_names") or []
# The generated parser reads its target width from this name rather
# than embedding it, so one cached parser serves every width.
field_count = int(job.get("field_count") or len(column_names) or 1)

try:
    # Split rows the same way the document was profiled
    records, _sheets = split_records(body_text)

    rows: List[List[str]] = []
    for record in records:
        try:
            rows.append(next(csv.reader(
                [record], delimiter=delimiter, quotechar=quote_char)))
        except (csv.Error, StopIteration):
            rows.append(record.split(delimiter))

    # Drop the header row, if one of the rows is the header
    if header_row_index is not None and 0 <= header_row_index < len(rows):
        data_rows = rows[header_row_index + 1:]
        first_data_offset = header_row_index + 2
    else:
        data_rows = rows
        first_data_offset = 1

    # Keep the rows the generated code can actually address. A worksheet often
    # holds a form or a second table alongside the one that was profiled, and
    # those rows are too short to contain the target indices; counting them as
    # extraction failures understates the success rate.
    #
    # The test is "long enough", not "same width as the header": trailing empty
    # cells are routinely trimmed, so most genuine rows are a field or two
    # shorter than the header.
    skipped_wrong_shape = 0
    if min_field_count:
        kept = []
        for offset, row in enumerate(data_rows):
            if len(row) >= min_field_count:
                kept.append((offset, row))
            else:
                skipped_wrong_shape += 1
        indexed_rows = kept
    else:
        indexed_rows = list(enumerate(data_rows))

    namespace = {
        'csv': csv,
        'json': json,
        'io': io,
        'List': List,
        'Dict': Dict,
        'Any': Any,
        'FIELD_COUNT': field_count,
    }

    exec(generated_code, namespace)

    DataExtractor = namespace.get('DataExtractor')
    if not DataExtractor:
        print(json.dumps({
            "status": "error",
            "error": "Generated code did not define DataExtractor class"
        }))
        sys.exit(1)

    def normalize(value):
        """Trim, and treat a blank cell as absent.

        The generated code is asked to do this too, and usually does. Doing it
        here as well makes it not matter which: strip() on an already-stripped
        value is a no-op, so code that got it right is unaffected, while code
        that skipped it stops producing a different answer from its siblings.

        Applied on the way out rather than on the way in. Cleaning the row
        before parse_row would hand the existing parsers a None where a blank
        cell was, and `v.strip() or None` raises on it.

        Anything that is not a string is passed through: the parsers emit None
        for padding, and the bookkeeping keys carry a bool and a list.
        """
        if not isinstance(value, str):
            return value
        return value.strip() or None

    def name_values(parsed):
        """Pair positions with column names.

        The generated code returns values in column order and never sees the
        names. Pairing them here, in code we control, means a name cannot be
        dropped, renamed or invented: position i always takes name i.
        """
        values = parsed.get('values')
        if values is None:
            # Code that already returns named fields: pass it through
            return {k: normalize(v) for k, v in parsed.items()}

        named = {}
        for index, value in enumerate(values):
            key = column_names[index] if index < len(column_names) else f'column_{index}'
            named[key] = normalize(value)
        named['_valid'] = parsed.get('_valid', True)
        named['_errors'] = parsed.get('_errors', [])
        # Values the script produced beyond the declared columns are still
        # reported rather than dropped, so a width mismatch is visible.
        if len(values) > len(column_names) and column_names:
            named['_extra_values'] = len(values) - len(column_names)
        return named

    results = []
    for offset, row in indexed_rows:
        row_number = first_data_offset + offset
        try:
            parsed = DataExtractor.parse_row(row)
            # Not named `records`: that holds the document's rows, and
            # shadowing it corrupts total_records in the result below.
            parsed_records = parsed if isinstance(parsed, list) else [parsed]
            for record in parsed_records:
                if not isinstance(record, dict):
                    continue
                named = name_values(record)
                named['_row_number'] = row_number
                results.append(named)
        except Exception as e:
            results.append({
                '_row_number': row_number,
                '_valid': False,
                '_errors': [str(e)]
            })

    print(json.dumps({
        "status": "success",
        "total_records": len(records),
        "total_rows": len(indexed_rows),
        "skipped_wrong_shape": skipped_wrong_shape,
        "extracted_rows": results
    }))

except Exception as e:
    print(json.dumps({
        "status": "error",
        "error": str(e)
    }))
    sys.exit(1)
