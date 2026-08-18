"""Tests for the structural detector (Tool 2).

The tool reports how a document is laid out. It makes no judgement about what
the columns mean, so nothing here asserts anything about PII.
"""

import json
from tools.delimiter_detector.tool import DelimiterDetectorTool


def _report(sample, **kwargs):
    tool = DelimiterDetectorTool()
    response = json.loads(tool({"raw_sample": sample, **kwargs}))
    return response


def test_detects_comma_delimiter():
    """A comma-delimited sample is reported as such, with header width."""
    sample = (
        "employee_id,first_name,last_name,email\n"
        "10001,John,Smith,john@company.com\n"
        "10002,Jane,Doe,jane@company.com"
    )
    r = _report(sample)

    assert r["status"] == "success"
    assert r["rejected"] is False
    report = r["metadata_report"]
    assert report["delimiter"] == ","
    assert report["delimiter_name"] == "comma"
    assert report["format"] == "csv"
    assert report["header_field_count"] == 4
    assert report["header_names"] == ["employee_id", "first_name", "last_name", "email"]
    assert report["data_row_count"] == 2
    assert report["ragged"] is False

    print("✓ test_detects_comma_delimiter PASSED")


def test_detects_pipe_and_tab():
    """Pipe and tab are distinguished from comma, even with commas in values."""
    pipe = "id|full name|city\n1|Smith, John|Vancouver\n2|Doe, Jane|Toronto"
    r = _report(pipe)["metadata_report"]
    assert r["delimiter"] == "|", r
    assert r["header_field_count"] == 3
    # The comma inside a value must not win
    assert r["delimiter_name"] == "pipe"

    tab = "id\tname\tcity\n1\tJohn\tVancouver\n2\tJane\tToronto"
    r2 = _report(tab)["metadata_report"]
    assert r2["delimiter"] == "\t"
    assert r2["header_field_count"] == 3

    print("✓ test_detects_pipe_and_tab PASSED")


def test_header_char_length_and_sheet_size():
    """Header length and sheet size are reported for the script generator."""
    header = "id,name,email"
    sample = f"{header}\n1,John,a@b.com\n2,Jane,c@d.com"
    r = _report(sample, total_records=5000, total_bytes=250000)["metadata_report"]

    assert r["header_char_length"] == len(header)
    assert r["header_field_count"] == 3
    # Whole-document size comes from Tool 1, not from the sample
    assert r["sheet_record_count"] == 5000
    assert r["sheet_byte_length"] == 250000
    assert r["sampled_record_count"] == 3

    print("✓ test_header_char_length_and_sheet_size PASSED")


def test_ragged_rows_are_reported():
    """Rows of differing width are flagged, with the range."""
    sample = "a,b,c,d\n1,2,3,4\n5,6,7\n8,9,10,11"
    r = _report(sample)["metadata_report"]

    assert r["ragged"] is True
    assert r["min_field_count"] == 3
    assert r["max_field_count"] == 4
    assert r["modal_field_count"] == 4

    print("✓ test_ragged_rows_are_reported PASSED")


def test_header_below_a_title_block():
    """A title above the table does not become the header."""
    sample = (
        "Quarterly Report\n"
        "id,name,email\n"
        "1,John,a@b.com\n"
        "2,Jane,c@d.com"
    )
    r = _report(sample)["metadata_report"]

    assert r["header_row_index"] == 1
    assert r["header_names"] == ["id", "name", "email"]

    print("✓ test_header_below_a_title_block PASSED")


def test_headerless_document_is_numbered_not_rejected():
    """Rows of pure data get numbered columns, and no row is treated as labels.

    Naming a column after whatever sat in row 0 put an employee's name on a
    column on real documents. The rows are still perfectly readable, so the
    document is kept and the columns are numbered instead.
    """
    sample = (
        "2835353,126548,Wesley Nutter,wes@x.com\n"
        "2995056,127071,Argemiro Correia,arg@x.com\n"
        "2999170,128001,Maria Garcia,mg@x.com"
    )
    r = _report(sample, guid="abc-123")

    assert r["status"] == "success"
    assert r["rejected"] is False, "a readable document must not be dropped"

    report = r["metadata_report"]
    assert report["header_source"] == "positional"
    assert report["header_names"] == ["column_0", "column_1", "column_2", "column_3"]
    assert report["header_row_index"] == -1      # no row is skipped as a header
    assert report["data_row_count"] == 3         # every row is data
    # Wesley Nutter must not have become a column name
    assert not any("Wesley" in n for n in report["header_names"])

    print("✓ test_headerless_document_is_numbered_not_rejected PASSED")


def test_header_in_sheet_name():
    """Some worksheets keep the header in the sheet name, with all rows data."""
    sample = (
        "Nasir Ganie,10226035,Power Extension\n"
        "Vishal LNU,10204561,HDMI Cable\n"
        "Khopu Ch,10238780,DP Converter"
    )
    r = _report(sample, sheet_names=["Username,WD ID,Asset"])

    assert r["rejected"] is False
    report = r["metadata_report"]
    assert report["header_source"] == "sheet_name"
    assert report["header_row_index"] == -1     # not a row in the document
    assert report["header_names"] == ["Username", "WD ID", "Asset"]

    print("✓ test_header_in_sheet_name PASSED")


def test_leaked_header_cell_is_flagged():
    """A header cell holding data is reported so the script can ignore its name."""
    sample = (
        "id,ramoncito@telus.com,city\n"
        "1,juan@telus.com,Vancouver\n"
        "2,gemma@telus.com,Toronto"
    )
    r = _report(sample)["metadata_report"]

    # Column 1's 'name' is an email address, like every value beneath it
    assert 1 in r["header_cells_that_look_like_data"]
    # Columns whose names are genuine are not flagged
    assert 0 not in r["header_cells_that_look_like_data"]

    print("✓ test_leaked_header_cell_is_flagged PASSED")


def test_printed_report_is_rejected_as_not_a_table():
    """Prose and printed reports must be refused, not extracted as one column.

    Real example: a renewal report whose rows are a title, a date range, a print
    timestamp and an address. Comma "won" the delimiter vote on 1 record in 12,
    and the document would have loaded as a single column of whole lines.

    Judged on the modal field count, not on delimiter confidence: legitimately
    ragged tables score low confidence too — a 187-column sheet scored 0.167 —
    so a confidence floor would reject real tables.
    """
    sample = (
        "Springfield Insurance Brokers 4,,,,,,,,,,,,,,Detailed Renewal Report\n"
        "2022-08-22 - 2024-11-27\n"
        "Print Date: 2024-11-27 8:23AM EST\n"
        "Tel: 416-359-9339\n"
        "Filtered By\n"
        "Show: All user presences"
    )
    r = _report(sample, guid="report-1")

    assert r["rejected"] is True
    assert r["rejection_code"] == "SINGLE_COLUMN"
    assert "not a table" in r["rejection_reason"]

    print("\u2713 test_printed_report_is_rejected_as_not_a_table PASSED")


def test_a_two_column_table_is_still_accepted():
    """The narrowest real table must not be caught by the same rule."""
    r = _report("id,name\n1,John\n2,Jane\n3,Bob")

    assert r["rejected"] is False
    assert r["metadata_report"]["modal_field_count"] == 2

    print("\u2713 test_a_two_column_table_is_still_accepted PASSED")


def test_blank_and_repeated_header_cells_are_made_unique():
    """Every column needs a distinct name or its data is lost downstream.

    Callers key rows by column name, so two columns sharing one collapse into a
    single key. A real document here had five columns whose header cells were
    ['Jaycee Jupuri', '10286126', '', '', ''] — three blanks — and it silently
    arrived as three columns instead of five. Repeats are just as common: a
    staff roster header ran THU, FRI, SAT ... THU, FRI, SAT week after week.
    """
    # blank cells in the middle of an otherwise real header
    blanks = _report(
        "agent,team,,,total\n"
        "alice,red,x,y,z\n"
        "bob,blue,x,y,z\n"
        "carol,green,x,y,z"
    )["metadata_report"]["header_names"]

    assert len(blanks) == len(set(blanks)), f"names must be unique: {blanks}"
    assert "" not in blanks
    assert blanks == ["agent", "team", "column_2", "column_3", "total"], blanks

    # and repeated names, as a weekly roster produces
    repeats = _report(
        "team,skill,THU,FRI,THU,FRI\n"
        "a,b,c,d,e,f\n"
        "g,h,i,j,k,l\n"
        "m,n,o,p,q,r"
    )["metadata_report"]["header_names"]

    assert len(repeats) == len(set(repeats)), f"names must be unique: {repeats}"
    assert repeats == ["team", "skill", "THU", "FRI", "THU_2", "FRI_2"], repeats

    print("\u2713 test_blank_and_repeated_header_cells_are_made_unique PASSED")


def test_real_header_names_are_left_alone():
    """A document with a good header must read exactly as it did before."""
    names = _report(
        "id,name,email\n1,John,a@b.com\n2,Jane,c@d.com"
    )["metadata_report"]["header_names"]

    assert names == ["id", "name", "email"]

    print("\u2713 test_real_header_names_are_left_alone PASSED")


def test_quote_char_is_detected_not_assumed():
    """The quote character is measured; a document that quotes nothing says so."""
    quoted = _report(
        'id,name,city\n"1","Smith, John","Vancouver"\n"2","Doe, Jane","Toronto"'
    )["metadata_report"]
    assert quoted["quote_char"] == '"', quoted["quote_char"]
    assert quoted["quoted_field_rows"] >= 2

    plain = _report("id,name,city\n1,John,Vancouver\n2,Jane,Toronto")["metadata_report"]
    assert plain["quote_char"] is None, "claiming a quote char it never uses is a fiction"
    assert plain["quoted_field_rows"] == 0

    single = _report(
        "id,name,city\n1,'Smith, John',Vancouver\n2,'Doe, Jane',Toronto"
    )["metadata_report"]
    assert single["quote_char"] == "'", single["quote_char"]

    print("\u2713 test_quote_char_is_detected_not_assumed PASSED")


def test_apostrophe_in_a_word_is_not_a_quote_char():
    """O'Brien must not be read as an opening quote."""
    r = _report("id,name\n1,O'Brien\n2,D'Arcy\n3,O'Neill")["metadata_report"]

    assert r["quote_char"] is None, r["quote_char"]
    assert r["modal_field_count"] == 2

    print("\u2713 test_apostrophe_in_a_word_is_not_a_quote_char PASSED")


def test_zero_width_characters_are_removed():
    """A byte-order mark must not end up inside a column name.

    str.strip() does not remove U+FEFF, so a BOM on the first header cell used
    to survive into the column name as an invisible character. It occurs about
    350,000 times across the corpus.
    """
    r = _report("\ufeffid,name,city\n1,John,Vancouver\n2,\u200bJane,Toronto")
    names = r["metadata_report"]["header_names"]

    assert names == ["id", "name", "city"], names
    assert not any("\ufeff" in n or "\u200b" in n for n in names)

    print("\u2713 test_zero_width_characters_are_removed PASSED")


def test_encoding_is_reported_not_invented():
    """body_text arrives already decoded, so encoding is passed through."""
    default = _report("id,name\n1,John\n2,Jane")["metadata_report"]
    assert default["encoding"] == "utf-8"
    assert default["has_non_ascii"] is False

    told = _report("id,name\n1,John\n2,Jane", encoding="latin-1")["metadata_report"]
    assert told["encoding"] == "latin-1", "what Tool 1 read it as, not a guess"

    accented = _report("id,nom\n1,Zoë\n2,Renée")["metadata_report"]
    assert accented["has_non_ascii"] is True

    print("\u2713 test_encoding_is_reported_not_invented PASSED")


def test_missing_sample_is_an_error():
    """No sample is a tool error, distinct from a rejected document."""
    tool = DelimiterDetectorTool()
    r = json.loads(tool({"raw_sample": ""}))
    assert r["status"] == "error"

    print("✓ test_missing_sample_is_an_error PASSED")


def run_all_tests():
    tests = [
        test_detects_comma_delimiter,
        test_detects_pipe_and_tab,
        test_header_char_length_and_sheet_size,
        test_ragged_rows_are_reported,
        test_header_below_a_title_block,
        test_headerless_document_is_numbered_not_rejected,
        test_header_in_sheet_name,
        test_leaked_header_cell_is_flagged,
        test_printed_report_is_rejected_as_not_a_table,
        test_a_two_column_table_is_still_accepted,
        test_blank_and_repeated_header_cells_are_made_unique,
        test_real_header_names_are_left_alone,
        test_quote_char_is_detected_not_assumed,
        test_apostrophe_in_a_word_is_not_a_quote_char,
        test_zero_width_characters_are_removed,
        test_encoding_is_reported_not_invented,
        test_missing_sample_is_an_error,
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
