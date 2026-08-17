"""Tests for map_to_schema (Tool 6).

Tool 6 decides which of a document's own columns correspond to schema fields and
rewrites the rows under those field names. The decision comes from the model, so
these tests stub it: what is being pinned here is how a mapping is applied, not
what the model chooses.
"""

import json
from tools.map_to_schema.tool import MapToSchemaTool
import extraction.column_labeler as labeler_module


class _StubLabeler:
    """Stands in for the model so the mapping logic can be tested alone."""

    def __init__(self, answers):
        self.answers = answers
        self.asked = None

    def label(self, columns):
        self.asked = columns
        return {name: self.answers.get(name) for name, _values in columns}


def _with_labeler(answers):
    stub = _StubLabeler(answers)
    labeler_module._instance = stub
    return stub


def teardown():
    labeler_module._instance = None


ROWS = [
    {"id": "1", "who": "Adam Fortuin", "mail": "adam@x.com",
     "note": "ok", "_valid": True, "_row_number": 2},
    {"id": "2", "who": "Jane Doe", "mail": "jane@x.com",
     "note": "", "_valid": True, "_row_number": 3},
]
REPORT = {"header_names": ["id", "who", "mail", "note"]}


def test_maps_columns_and_rewrites_rows():
    """Mapped columns come out under their schema field names."""
    _with_labeler({"who": "PERSON_FULL_NAME", "mail": "PERSON_EMAIL"})
    r = json.loads(MapToSchemaTool()({
        "guid": "g", "extracted_rows": ROWS, "metadata_report": REPORT,
    }))

    assert r["status"] == "success"
    assert r["mapped_column_count"] == 2
    assert r["unmapped_column_count"] == 2      # 'id' and 'note' map nowhere
    row = r["mapped_rows"][0]
    assert row["PERSON_FULL_NAME"] == "Adam Fortuin"
    assert row["PERSON_EMAIL"] == "adam@x.com"
    # unmapped columns are not carried through under their own names
    assert "note" not in row and "id" not in row
    assert row["_row_number"] == 2

    teardown()
    print("✓ test_maps_columns_and_rewrites_rows PASSED")


def test_columns_are_offered_with_their_values():
    """The model sees values, which is the only evidence a numbered column has."""
    stub = _with_labeler({})
    MapToSchemaTool()({
        "guid": "g", "extracted_rows": ROWS, "metadata_report": REPORT,
    })

    asked = dict(stub.asked)
    assert asked["mail"] == ["adam@x.com", "jane@x.com"], asked["mail"]
    # An empty value is not offered as evidence
    assert asked["note"] == ["ok"], asked["note"]

    teardown()
    print("✓ test_columns_are_offered_with_their_values PASSED")


def test_numbered_columns_can_still_be_mapped():
    """A headerless document maps from content alone."""
    _with_labeler({"column_2": "PERSON_EMAIL"})
    rows = [{"column_0": "x", "column_2": "a@b.com", "_valid": True, "_row_number": 1}]
    r = json.loads(MapToSchemaTool()({
        "guid": "g", "extracted_rows": rows,
        "metadata_report": {"header_names": ["column_0", "column_1", "column_2"]},
    }))

    assert r["mapped_rows"][0]["PERSON_EMAIL"] == "a@b.com"

    teardown()
    print("✓ test_numbered_columns_can_still_be_mapped PASSED")


def test_two_columns_claiming_one_field_is_reported():
    """Ambiguity is surfaced, not hidden: the first column wins and it is logged."""
    _with_labeler({"who": "PERSON_EMAIL", "mail": "PERSON_EMAIL"})
    r = json.loads(MapToSchemaTool()({
        "guid": "g", "extracted_rows": ROWS, "metadata_report": REPORT,
    }))

    assert r["duplicate_target_fields"] == ["PERSON_EMAIL"]
    # 'who' comes first in the document, so its value is the one kept
    assert r["mapped_rows"][0]["PERSON_EMAIL"] == "Adam Fortuin"

    teardown()
    print("✓ test_two_columns_claiming_one_field_is_reported PASSED")


def test_no_schema_match_yields_no_rows():
    """A document with nothing of interest produces nothing, and says so."""
    _with_labeler({})
    r = json.loads(MapToSchemaTool()({
        "guid": "g", "extracted_rows": ROWS, "metadata_report": REPORT,
    }))

    assert r["status"] == "success"
    assert r["mapped_rows"] == []
    assert r["mapped_column_count"] == 0
    assert "schema field" in r["message"]

    teardown()
    print("✓ test_no_schema_match_yields_no_rows PASSED")


def test_no_rows_is_not_an_error():
    """Nothing to map is a quiet no-op, not a failure."""
    _with_labeler({})
    r = json.loads(MapToSchemaTool()({
        "guid": "g", "extracted_rows": [], "metadata_report": REPORT,
    }))

    assert r["status"] == "success"
    assert r["mapped_rows"] == []

    teardown()
    print("✓ test_no_rows_is_not_an_error PASSED")


def run_all_tests():
    tests = [
        test_maps_columns_and_rewrites_rows,
        test_columns_are_offered_with_their_values,
        test_numbered_columns_can_still_be_mapped,
        test_two_columns_claiming_one_field_is_reported,
        test_no_schema_match_yields_no_rows,
        test_no_rows_is_not_an_error,
    ]
    passed = failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"✗ {test.__name__} FAILED: {e}")
            failed += 1
        finally:
            teardown()

    print("\n" + "=" * 60)
    print(f"Test Results: {passed}/{len(tests)} passed, {failed} failed")
    print("=" * 60)
    return failed == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if run_all_tests() else 1)
