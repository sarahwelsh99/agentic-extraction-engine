"""Demo: Header detection at different row positions."""
import json
import tempfile
import os

from tools.fetch_and_sample.tool import FetchAndSampleTool


def print_section(title):
    """Print a formatted section header."""
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")


def demo_file_with_metadata():
    """Demo: File with metadata comments before headers."""
    print_section("SCENARIO 1: File with Metadata Comments")

    # Create a file with metadata before headers
    csv_content = """# Report: User Database Export
# Generated: 2026-08-12
# Version: 1.0
id,name,email
1,Alice,alice@test.com
2,Bob,bob@test.com
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write(csv_content)
        temp_path = f.name

    try:
        tool = FetchAndSampleTool()

        print("FILE CONTENTS:")
        print(csv_content)

        print("\n" + "-" * 70)
        print("APPROACH 1: Default (assumes headers on row 0)")
        print("-" * 70)
        response1 = json.loads(tool({"source_path": temp_path}))
        print(f"Header row index: {response1['actual_header_row_index']}")
        print(f"First line detected as header: {response1['first_line_is_header']}")
        print(f"Problem: Row 0 is a comment, not the header! ✗")

        print("\n" + "-" * 70)
        print("APPROACH 2: Explicit position (you know headers are at row 3)")
        print("-" * 70)
        response2 = json.loads(tool({
            "source_path": temp_path,
            "header_row_index": 3,
        }))
        print(f"Header row index: {response2['actual_header_row_index']}")
        print(f"First line detected as header: {response2['first_line_is_header']}")
        print(f"Solution: Specify the correct row. ✓")

        print("\n" + "-" * 70)
        print("APPROACH 3: Heuristic search (auto-detect headers)")
        print("-" * 70)
        response3 = json.loads(tool({
            "source_path": temp_path,
            "find_header_heuristic": True,
        }))
        print(f"Header row index: {response3['actual_header_row_index']}")
        print(f"First line detected as header: {response3['first_line_is_header']}")
        print(f"Solution: Tool searches and finds the real headers automatically. ✓")

    finally:
        os.unlink(temp_path)


def demo_numeric_data_before_headers():
    """Demo: Numeric data before actual headers."""
    print_section("SCENARIO 2: Numeric Data Before Headers")

    csv_content = """100,200,300
50,60,70
id,value,count
1,100,5
2,200,10
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write(csv_content)
        temp_path = f.name

    try:
        tool = FetchAndSampleTool()

        print("FILE CONTENTS:")
        print(csv_content)

        print("\n" + "-" * 70)
        print("Heuristic detects that rows 0-1 are numeric data, not headers")
        print("-" * 70)
        response = json.loads(tool({
            "source_path": temp_path,
            "find_header_heuristic": True,
        }))
        print(f"Header row index found: {response['actual_header_row_index']}")
        print(f"Headers: id,value,count")
        print(f"Why row 2: Contains keywords (id, value, count) and looks like labels ✓")

    finally:
        os.unlink(temp_path)


def demo_standard_csv():
    """Demo: Standard CSV (no special case)."""
    print_section("SCENARIO 3: Standard CSV (Headers on Row 0)")

    csv_content = """id,name,email
1,Alice,alice@test.com
2,Bob,bob@test.com
3,Charlie,charlie@test.com
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write(csv_content)
        temp_path = f.name

    try:
        tool = FetchAndSampleTool()

        print("FILE CONTENTS:")
        print(csv_content)

        print("\n" + "-" * 70)
        print("Default behavior: Just use row 0")
        print("-" * 70)
        response = json.loads(tool({"source_path": temp_path}))
        print(f"Header row index: {response['actual_header_row_index']}")
        print(f"Headers at row 0: id,name,email ✓")
        print(f"No extra work needed!")

    finally:
        os.unlink(temp_path)


def demo_keyword_scoring():
    """Demo: How the heuristic scores rows."""
    print_section("SCENARIO 4: Heuristic Scoring Logic")

    print("When find_header_heuristic=true, Tool 1 scores each row:\n")

    examples = [
        ("100,200,300", "Numeric data (all numbers)", -1.5),
        ("garbage,junk,waste", "No keywords", 1.5),
        ("user_id,name,email", "Has id, name, email keywords", 7.0),
        ("user_id,full_name,contact_email", "More keywords + underscores", 8.0),
    ]

    for row, description, expected_score in examples:
        print(f"  Row: {row:30} | {description:35} | Score: {expected_score}")

    print("\n  Winner: Row with highest score → Row with keywords like 'id', 'name', 'email'")


def main():
    """Run all demos."""
    print("\n" + "🎯 " * 35)
    print("HEADER DETECTION DEMO")
    print("🎯 " * 35)

    print("""
This demo shows how Tool 1 handles headers at different row positions.
Problem: Not all CSV files have headers on row 0!
""")

    demo_standard_csv()
    demo_file_with_metadata()
    demo_numeric_data_before_headers()
    demo_keyword_scoring()

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print("""
Tool 1 now supports THREE ways to handle headers:

1. DEFAULT: Headers on row 0 (fastest, simplest)
   → Use when: You know headers are first row

2. EXPLICIT: Specify header_row_index
   → Use when: You know exactly where headers are

3. HEURISTIC: Set find_header_heuristic=true
   → Use when: Headers could be anywhere; let Tool 1 find them

All responses include 'actual_header_row_index' so downstream tools
know exactly which row to treat as the header.
""")

    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
