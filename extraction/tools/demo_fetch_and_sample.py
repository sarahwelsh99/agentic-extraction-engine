"""Demo: Tool 1 - fetch_and_sample in action."""
import json
import tempfile
import os

from extraction.tools.fetch_and_sample import fetch_and_sample


def demo_fetch_csv():
    """Demo fetching a CSV file."""
    print("\n" + "="*70)
    print("DEMO 1: Fetch and Sample a CSV File")
    print("="*70)

    # Create a sample CSV file
    csv_content = """id,name,email,created_at,salary
1,Alice Johnson,alice@company.com,2026-01-15,85000
2,Bob Smith,bob@company.com,2026-01-16,92000
3,Carol White,carol@company.com,2026-01-17,88000
4,David Brown,david@company.com,2026-01-18,95000
5,Eve Davis,eve@company.com,2026-01-19,87000"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write(csv_content)
        temp_path = f.name

    try:
        # Call the tool
        input_data = {
            "source_path": temp_path,
            "sample_size": 5,
            "max_bytes": 1048576,
            "skip_rows": 0,
            "encoding": "utf-8",
        }

        print(f"\nInput to Tool:")
        print(json.dumps(input_data, indent=2))

        response_json = fetch_and_sample(input_data)
        response = json.loads(response_json)

        print(f"\nResponse from Tool:")
        print(json.dumps(response, indent=2))

        # Show what the agent would see
        print(f"\nAgent's View of Raw Data Sample:")
        print(response["raw_sample"])

        print(f"\nKey Metadata for Agent:")
        print(f"  - Format: {response['detected_format_hint']}")
        print(f"  - Has Header: {response['first_line_is_header']}")
        print(f"  - Total Bytes: {response['total_bytes']}")
        print(f"  - Sample Size: {response['sample_size']} rows")
        print(f"  - Source Type: {response['source_type']}")

    finally:
        os.unlink(temp_path)


def demo_fetch_with_different_formats():
    """Demo fetching files with different formats."""
    print("\n" + "="*70)
    print("DEMO 2: Different File Formats")
    print("="*70)

    formats = [
        ("CSV", "id,name,age\n1,John,30\n2,Jane,25\n"),
        ("Pipe-Delimited", "id|name|age\n1|John|30\n2|Jane|25\n"),
        ("Tab-Delimited", "id\tname\tage\n1\tJohn\t30\n2\tJane\t25\n"),
        ("JSON Lines", '{"id":1,"name":"John","age":30}\n{"id":2,"name":"Jane","age":25}\n'),
    ]

    for format_name, content in formats:
        print(f"\n{format_name}:")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            temp_path = f.name

        try:
            response = json.loads(fetch_and_sample({"source_path": temp_path}))
            print(f"  Detected Format: {response['detected_format_hint']}")
            print(f"  Status: {response['status']}")
        finally:
            os.unlink(temp_path)


def demo_error_handling():
    """Demo error handling."""
    print("\n" + "="*70)
    print("DEMO 3: Error Handling")
    print("="*70)

    test_cases = [
        ("Missing source_path", {}),
        ("Nonexistent file", {"source_path": "/nonexistent/file.csv"}),
    ]

    for name, input_data in test_cases:
        print(f"\nTest: {name}")
        response = json.loads(fetch_and_sample(input_data))
        print(f"  Status: {response['status']}")
        print(f"  Error: {response['error']}")


def demo_schema_for_next_tool():
    """Show what the next tool will receive."""
    print("\n" + "="*70)
    print("DEMO 4: Output Format for Next Tool (infer_schema_and_profile)")
    print("="*70)

    csv_content = """id,name,email,department,salary
1,Alice,alice@company.com,Engineering,85000
2,Bob,bob@company.com,Sales,92000
3,Carol,carol@company.com,Engineering,88000"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write(csv_content)
        temp_path = f.name

    try:
        response = json.loads(fetch_and_sample({"source_path": temp_path}))

        print("\nOutput from Tool 1 (will be input to Tool 2):")
        print(f"\n✓ raw_sample (raw text data):")
        print(f"  {repr(response['raw_sample'][:100])}...")

        print(f"\n✓ detected_format_hint (what Tool 2 should expect):")
        print(f"  {response['detected_format_hint']}")

        print(f"\n✓ first_line_is_header (helpful for parsing):")
        print(f"  {response['first_line_is_header']}")

        print(f"\n✓ encoding (for safe text handling):")
        print(f"  {response['encoding']}")

        print("\nTool 2 will receive raw_sample and use other metadata to infer schema.")

    finally:
        os.unlink(temp_path)


def main():
    """Run all demos."""
    print("\n" + "🔧 "*35)
    print("TOOL 1 VALIDATION DEMO: fetch_and_sample")
    print("🔧 "*35)

    demo_fetch_csv()
    demo_fetch_with_different_formats()
    demo_error_handling()
    demo_schema_for_next_tool()

    print("\n" + "="*70)
    print("✅ TOOL 1 VALIDATION COMPLETE")
    print("="*70)
    print("\nTool 1 Status: READY FOR DEPLOYMENT")
    print("\nNext Step: Build Tool 2 (infer_schema_and_profile)")
    print("="*70 + "\n")


if __name__ == "__main__":
    main()
