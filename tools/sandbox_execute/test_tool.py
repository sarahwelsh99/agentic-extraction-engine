"""Tests for sandbox_execute (Tool 4).

Tool 4 runs a generated script and reports what came out. Whether the result is
good enough is Tool 5's decision, so nothing here asserts on quality.
"""

import json
from tools.sandbox_execute.tool import SandboxExecuteTool


def test_error_handling_missing_inputs():
    """Missing generated_code or missing body_text are both refused outright."""
    tool = SandboxExecuteTool()

    no_code = json.loads(tool({
        "guid": "test-guid",
        "body_text": "id,name\n1,John",
    }))
    assert no_code["status"] == "error"
    assert "generated_code" in no_code["error"].lower()

    no_body = json.loads(tool({
        "guid": "test-guid",
        "generated_code": "class DataExtractor: pass",
    }))
    assert no_body["status"] == "error"
    assert "body_text" in no_body["error"].lower()

    print("\u2713 test_error_handling_missing_inputs PASSED")


def test_executes_and_names_columns():
    """The sandbox pairs positions with names; the script never sees them.

    The script returns values in column order. Naming here, in code we control,
    is what makes it impossible for a column to be dropped or renamed.
    """
    tool = SandboxExecuteTool()

    code = (
        "from typing import Dict, Any, List\n"
        "class DataExtractor:\n"
        "    @staticmethod\n"
        "    def parse_row(row: List[str]) -> Dict[str, Any]:\n"
        "        values = [None] * 3\n"
        "        for i in range(min(len(row), 3)):\n"
        "            values[i] = row[i].strip() or None\n"
        "        return {'values': values, '_valid': True, '_errors': []}\n"
    )
    response = json.loads(tool({
        "guid": "test-guid",
        "generated_code": code,
        "body_text": "id,name,email\n\n1,John,john@x.com\n\n2,Jane,jane@x.com",
        "metadata_report": {
            "delimiter": ",",
            "header_row_index": 0,
            "header_names": ["id", "name", "email"],
            "modal_field_count": 3,
        },
    }))

    assert response["status"] == "success", response
    rows = response["extracted_rows"]
    assert len(rows) == 2, rows
    assert rows[0]["name"] == "John", rows[0]
    assert rows[0]["email"] == "john@x.com", rows[0]
    # every declared column is present, none invented
    assert {"id", "name", "email"} <= set(rows[0])

    print("\u2713 test_executes_and_names_columns PASSED")


def test_unnamed_columns_fall_back_to_position():
    """More values than declared names must not be silently dropped."""
    tool = SandboxExecuteTool()

    code = (
        "from typing import Dict, Any, List\n"
        "class DataExtractor:\n"
        "    @staticmethod\n"
        "    def parse_row(row: List[str]) -> Dict[str, Any]:\n"
        "        return {'values': list(row), '_valid': True, '_errors': []}\n"
    )
    response = json.loads(tool({
        "guid": "test-guid",
        "generated_code": code,
        "body_text": "a,b,c\n\n1,2,3",
        "metadata_report": {
            "delimiter": ",",
            "header_row_index": 0,
            "header_names": ["a"],          # only one name declared
            "modal_field_count": 3,
        },
    }))

    assert response["status"] == "success", response
    row = response["extracted_rows"][0]
    assert row["a"] == "1"
    assert row["column_1"] == "2"          # numbered rather than lost
    assert row["column_2"] == "3"
    assert row["_extra_values"] == 2       # and the mismatch is reported

    print("\u2713 test_unnamed_columns_fall_back_to_position PASSED")


MESSY_ROW_INPUTS = {
    "guid": "test-guid",
    "body_text": "id,name,shift\n\n  10259240 ,,Adam\n\n 7 , Bo , 08:00-16:30 ",
    "metadata_report": {
        "delimiter": ",",
        "header_row_index": 0,
        "header_names": ["id", "name", "shift"],
        "modal_field_count": 3,
    },
}

# What every parser must produce for MESSY_ROW_INPUTS, whichever way it is
# written: trimmed, and a blank cell reported as absent rather than as "".
MESSY_ROW_EXPECTED = [
    {"id": "10259240", "name": None, "shift": "Adam"},
    {"id": "7", "name": "Bo", "shift": "08:00-16:30"},
]


def test_untrimmed_script_matches_trimming_one():
    """Trimming is the sandbox's job, so a script that skips it cannot differ.

    Four parsers were generated for the same work and three trimmed while one
    did not, so two documents loaded with stray spaces and empty strings where
    the rest had clean values. Nothing caught it: the evaluator counts rows and
    columns, and the counts were perfect. Normalising on the way out makes the
    difference unrepresentable rather than merely unlikely.
    """
    tool = SandboxExecuteTool()

    trims = (
        "from typing import Dict, Any, List\n"
        "class DataExtractor:\n"
        "    @staticmethod\n"
        "    def parse_row(row: List[str]) -> Dict[str, Any]:\n"
        "        values = [v.strip() or None for v in row][:FIELD_COUNT]\n"
        "        values += [None] * (FIELD_COUNT - len(values))\n"
        "        return {'values': values, '_valid': True, '_errors': []}\n"
    )
    # The defect verbatim: no strip, no blank-to-absent
    does_not_trim = (
        "from typing import Dict, Any, List\n"
        "class DataExtractor:\n"
        "    @staticmethod\n"
        "    def parse_row(row: List[str]) -> Dict[str, Any]:\n"
        "        values = list(row)[:FIELD_COUNT]\n"
        "        values += [None] * (FIELD_COUNT - len(values))\n"
        "        return {'values': values, '_valid': True, '_errors': []}\n"
    )

    outputs = []
    for code in (trims, does_not_trim):
        response = json.loads(tool({**MESSY_ROW_INPUTS, "generated_code": code}))
        assert response["status"] == "success", response
        outputs.append([
            {k: v for k, v in row.items() if not k.startswith("_")}
            for row in response["extracted_rows"]
        ])

    assert outputs[0] == MESSY_ROW_EXPECTED, outputs[0]
    assert outputs[1] == MESSY_ROW_EXPECTED, outputs[1]
    assert outputs[0] == outputs[1], outputs

    print("✓ test_untrimmed_script_matches_trimming_one PASSED")


def test_named_field_scripts_are_normalized_too():
    """The pass-through branch gets the same treatment as the values branch.

    A script may return named fields directly instead of a values list. That
    path skips position-to-name pairing, so it would also have skipped
    normalising, and the defect would survive in scripts written that way.
    """
    tool = SandboxExecuteTool()

    code = (
        "from typing import Dict, Any, List\n"
        "class DataExtractor:\n"
        "    @staticmethod\n"
        "    def parse_row(row: List[str]) -> Dict[str, Any]:\n"
        "        return {'id': row[0], 'name': row[1], 'shift': row[2],\n"
        "                '_valid': True, '_errors': []}\n"
    )
    response = json.loads(tool({**MESSY_ROW_INPUTS, "generated_code": code}))

    assert response["status"] == "success", response
    rows = [
        {k: v for k, v in row.items() if not k.startswith("_")}
        for row in response["extracted_rows"]
    ]
    assert rows == MESSY_ROW_EXPECTED, rows
    # Bookkeeping is not a string and must survive normalising untouched
    assert response["extracted_rows"][0]["_valid"] is True
    assert response["extracted_rows"][0]["_errors"] == []

    print("✓ test_named_field_scripts_are_normalized_too PASSED")


def test_footer_lines_are_skipped():
    """A footer reported by the Looker never reaches parse_row or counts as data."""
    tool = SandboxExecuteTool()

    code = (
        "from typing import Dict, Any, List\n"
        "class DataExtractor:\n"
        "    @staticmethod\n"
        "    def parse_row(row: List[str]) -> Dict[str, Any]:\n"
        "        values = [v.strip() or None for v in row][:FIELD_COUNT]\n"
        "        values += [None] * (FIELD_COUNT - len(values))\n"
        "        return {'values': values, '_valid': True, '_errors': []}\n"
    )
    response = json.loads(tool({
        "guid": "test-guid",
        "generated_code": code,
        "body_text": "id,name\n1,John\n2,Jane\nTotal: 2 rows\nConfidential",
        "metadata_report": {
            "delimiter": ",",
            "header_row_index": 0,
            "header_names": ["id", "name"],
            "modal_field_count": 2,
            "footer_start_from_bottom": 2,
        },
    }))

    assert response["status"] == "success", response
    rows = response["extracted_rows"]
    assert len(rows) == 2, rows
    assert response["total_rows"] == 2, "the footer must not count as data"

    print("✓ test_footer_lines_are_skipped PASSED")


def run_all_tests():
    tests = [
        test_error_handling_missing_inputs,
        test_footer_lines_are_skipped,
        test_executes_and_names_columns,
        test_unnamed_columns_fall_back_to_position,
        test_untrimmed_script_matches_trimming_one,
        test_named_field_scripts_are_normalized_too,
    ]
    passed = failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"\u2717 {test.__name__} FAILED: {e}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"Test Results: {passed}/{len(tests)} passed, {failed} failed")
    print("=" * 60)
    return failed == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if run_all_tests() else 1)
