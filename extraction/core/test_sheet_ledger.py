"""Tests for extraction/core/sheet_ledger.py.

Durable per-guid sheet detail: one row per sheet, its tabular/rejection
status, and its PII flag - separate from workqueue.py's transient bin-packing
checkpoint, which gets archived away, not queried over time.
"""

import os
import tempfile

from extraction.core.sheet_ledger import SheetLedger


def _ledger():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)  # SheetLedger creates it fresh
    return SheetLedger(path)


SHEETS_5 = [
    {"sheet_name": f"Sheet{i}", "status": "success", "rejection_code": None,
     "stage_failed": None, "failure_reason": None, "rows_extracted": 10 + i,
     "has_pii": i % 2 == 0, "pii_score": i, "pii_signals": "DOB" if i % 2 == 0 else ""}
    for i in range(5)
]


def test_one_guid_with_five_sheets_produces_five_rows():
    ledger = _ledger()
    ledger.record_sheets("g1", SHEETS_5)

    rows = ledger.sheets_for("g1")
    assert len(rows) == 5
    assert [r["sheet_name"] for r in rows] == ["Sheet0", "Sheet1", "Sheet2", "Sheet3", "Sheet4"]
    assert [r["sheet_index"] for r in rows] == [0, 1, 2, 3, 4]

    print("✓ test_one_guid_with_five_sheets_produces_five_rows PASSED")


def test_has_pii_and_rejection_details_are_stored():
    ledger = _ledger()
    ledger.record_sheets("g1", [
        {"sheet_name": "Prose", "status": "rejected", "rejection_code": "NOT_TABULAR",
         "stage_failed": "look", "failure_reason": "prose, not a table",
         "rows_extracted": 0, "has_pii": True, "pii_score": 2, "pii_signals": "DOB,ADDRESS"},
    ])

    [row] = ledger.sheets_for("g1")
    assert row["status"] == "rejected"
    assert row["rejection_code"] == "NOT_TABULAR", "not going for extraction because it's not tabular"
    assert row["has_pii"] == 1
    assert row["pii_score"] == 2
    assert row["pii_signals"] == "DOB,ADDRESS"

    print("✓ test_has_pii_and_rejection_details_are_stored PASSED")


def test_reprocessing_a_guid_replaces_its_rows_not_accumulates():
    ledger = _ledger()
    ledger.record_sheets("g1", SHEETS_5)
    assert len(ledger.sheets_for("g1")) == 5

    ledger.record_sheets("g1", SHEETS_5[:2])
    rows = ledger.sheets_for("g1")
    assert len(rows) == 2, "a re-run must replace the old set, not add to it"

    print("✓ test_reprocessing_a_guid_replaces_its_rows_not_accumulates PASSED")


def test_different_guids_do_not_interfere():
    ledger = _ledger()
    ledger.record_sheets("g1", SHEETS_5[:2])
    ledger.record_sheets("g2", SHEETS_5[:3])

    assert len(ledger.sheets_for("g1")) == 2
    assert len(ledger.sheets_for("g2")) == 3

    print("✓ test_different_guids_do_not_interfere PASSED")


def run_all_tests():
    tests = [
        test_one_guid_with_five_sheets_produces_five_rows,
        test_has_pii_and_rejection_details_are_stored,
        test_reprocessing_a_guid_replaces_its_rows_not_accumulates,
        test_different_guids_do_not_interfere,
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
