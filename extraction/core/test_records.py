"""Tests for extraction/core/records.py's sheet detection and splitting.

split_records() already recognized SHEET_MARKER rows but discarded where a
sheet's data begins and ends. These tests pin split_sheets()/
has_multiple_sheets(), which preserve that boundary - confirmed against the
real shape found in production (a workbook with one sheet per agent, each
with its own header, e.g. guid 3d4e39dc-9d38-249d-9ab5-0deff55e817a).
"""

from extraction.core.records import (
    ROW_SEPARATOR, SHEET_MARKER, has_multiple_sheets, split_sheets,
)


def _sheet_rows(name: str, header: str, *data_rows: str) -> str:
    """One sheet's rows, in the flattened form glean actually produces:
    a marker-terminated name row, then a marker-terminated header row."""
    return ROW_SEPARATOR.join([f"{name}{SHEET_MARKER}", f"{header}{SHEET_MARKER}", *data_rows])


def test_ordinary_document_is_a_single_unnamed_sheet():
    """No SHEET_MARKER rows at all: the common case, single-sheet, untouched."""
    body = "id,name\n1,John\n2,Jane"

    assert has_multiple_sheets(body) is False
    blocks = split_sheets(body)
    assert len(blocks) == 1
    assert blocks[0].name is None
    assert blocks[0].body_text == body

    print("✓ test_ordinary_document_is_a_single_unnamed_sheet PASSED")


def test_single_sheet_marker_is_still_single_sheet():
    """One sheet-name marker, no second sheet - not multi-sheet."""
    body = _sheet_rows("Only Sheet", "id,name", "1,John", "2,Jane")

    assert has_multiple_sheets(body) is False
    blocks = split_sheets(body)
    assert len(blocks) == 1
    assert blocks[0].name == "Only Sheet"

    print("✓ test_single_sheet_marker_is_still_single_sheet PASSED")


def test_multiple_sheets_are_split_with_their_own_header_and_data():
    """The real shape: name marker, then a header marker, then data rows -
    repeated per sheet. Each sheet's own header becomes its own first line."""
    body = ROW_SEPARATOR.join([
        _sheet_rows("Andreea Dobrica", "Week,TL,Agent", "51,x,y", "52,x,z"),
        _sheet_rows("Andreea Serghievici", "Week,TL,Agent,Actions", "9,a,b,c"),
    ])

    assert has_multiple_sheets(body) is True
    blocks = split_sheets(body)

    assert len(blocks) == 2
    assert blocks[0].name == "Andreea Dobrica"
    assert blocks[0].body_text.split(ROW_SEPARATOR) == ["Week,TL,Agent", "51,x,y", "52,x,z"]
    assert blocks[1].name == "Andreea Serghievici"
    assert blocks[1].body_text.split(ROW_SEPARATOR) == ["Week,TL,Agent,Actions", "9,a,b,c"]

    print("✓ test_multiple_sheets_are_split_with_their_own_header_and_data PASSED")


def test_each_sheets_tail_is_its_own_not_the_documents():
    """A sheet's last line must be that sheet's own last line, not a slice of
    the whole document's end - this is what fixes the Micro-Slicer's tail
    window only ever seeing the last sheet's footer."""
    body = ROW_SEPARATOR.join([
        _sheet_rows("First", "h1,h2", "a,a", "SHEET-ONE-TAIL,,"),
        _sheet_rows("Last", "h1,h2", "b,b", "SHEET-TWO-TAIL,,"),
    ])

    blocks = split_sheets(body)
    assert blocks[0].body_text.split(ROW_SEPARATOR)[-1] == "SHEET-ONE-TAIL,,"
    assert blocks[1].body_text.split(ROW_SEPARATOR)[-1] == "SHEET-TWO-TAIL,,"

    print("✓ test_each_sheets_tail_is_its_own_not_the_documents PASSED")


def test_empty_document_is_a_single_empty_sheet():
    assert split_sheets("") == split_sheets(None)
    blocks = split_sheets("")
    assert len(blocks) == 1
    assert blocks[0].name is None

    print("✓ test_empty_document_is_a_single_empty_sheet PASSED")


def run_all_tests():
    tests = [
        test_ordinary_document_is_a_single_unnamed_sheet,
        test_single_sheet_marker_is_still_single_sheet,
        test_multiple_sheets_are_split_with_their_own_header_and_data,
        test_each_sheets_tail_is_its_own_not_the_documents,
        test_empty_document_is_a_single_empty_sheet,
    ]
    passed = failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"✗ {test.__name__} FAILED: {e}")
            failed += 1
    print("\n" + "=" * 60)
    print(f"Test Results: {passed}/{len(tests)} passed, {failed} failed")
    print("=" * 60)
    return failed == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if run_all_tests() else 1)
