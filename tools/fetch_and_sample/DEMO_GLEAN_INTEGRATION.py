"""Demo: Using Tool 1 with glean.drive_files documents."""
import json


def print_section(title):
    """Print formatted section."""
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")


def demo_glean_document_flow():
    """Demo: Full flow from glean to agentic pipeline."""
    print_section("GLEAN DOCUMENT → AGENTIC PIPELINE FLOW")

    print("""
STEP 1: Fetch document from glean.drive_files
────────────────────────────────────────────

BigQuery Query:
    SELECT guid, title, body_text
    FROM glean.drive_files
    WHERE guid = 'ddffbdb6-5041-4d65-a744-5a0631a629aa'

Result:
    guid: ddffbdb6-5041-4d65-a744-5a0631a629aa
    title: SA Report Payroll_June 2025
    body_text: Location,Employee ID,Legal First Name,...
               ZA - Cape Town, 10259248,Aphindiwe,...
               ...

────────────────────────────────────────────
STEP 2: Pass to Tool 1 (fetch_and_sample)
────────────────────────────────────────────

Instead of:
    {
      "source_path": "gs://bucket/file.csv"
    }

Now use:
    {
      "guid": "ddffbdb6-5041-4d65-a744-5a0631a629aa",
      "body_text": "Location,Employee ID,Legal First Name,...",
      "sample_size": 10,
      "find_header_heuristic": false
    }

────────────────────────────────────────────
STEP 3: Tool 1 returns sampled structured data
────────────────────────────────────────────

Response:
{
  "status": "success",
  "guid": "ddffbdb6-5041-4d65-a744-5a0631a629aa",
  "source_type": "glean_document",
  "raw_sample": "Location,Employee ID,Legal First Name,...\\n
                ZA - Cape Town, 10259248,Aphindiwe,...\\n
                ...",
  "detected_format_hint": "csv",
  "first_line_is_header": true,
  "actual_header_row_index": 0,
  "total_bytes": 251117,
  "sample_size": 10,
  ...
}

────────────────────────────────────────────
STEP 4: Pass to Tool 2 (delimiter_detector)
────────────────────────────────────────────

Tool 2 receives:
  - raw_sample: The CSV data
  - detected_format_hint: "csv"
  - actual_header_row_index: 0
  - guid: Document reference

Tool 2 analyzes:
  - Extracts column names from row 0
  - Profiles each column (data types, patterns, PII fields)
  - Returns schema

────────────────────────────────────────────
STEP 5+: Continue through pipeline
────────────────────────────────────────────

Tool 3: Generate parsing code
Tool 4: Test the parser
Tool 5: Load validated data to BigQuery
    """)


def demo_input_comparison():
    """Show input format changes."""
    print_section("INPUT FORMAT COMPARISON")

    print("""
┌─────────────────────────────────────────────────────┐
│  BEFORE: Tool 1 accepts file paths                  │
└─────────────────────────────────────────────────────┘

{
  "source_path": "/home/data.csv"
}

{
  "source_path": "gs://bucket/data.csv"
}

{
  "source_path": "project.dataset.table"
}


┌─────────────────────────────────────────────────────┐
│  AFTER: Tool 1 also accepts body_text directly      │
└─────────────────────────────────────────────────────┘

{
  "body_text": "id,name,email\\n1,Alice,alice@...",
  "sample_size": 10
}

{
  "guid": "ddffbdb6-5041-4d65-a744-5a0631a629aa",
  "body_text": "Location,Employee ID,...",
  "sample_size": 10,
  "find_header_heuristic": false
}

{
  "source_path": "/home/data.csv",
  "sample_size": 10
}  ← Still works (backward compatible)


┌─────────────────────────────────────────────────────┐
│  KEY DIFFERENCE                                      │
└─────────────────────────────────────────────────────┘

OLD: Tool 1 fetches the file
    source_path → [Tool 1 reads file] → sample

NEW: Agent fetches, Tool 1 processes
    BigQuery → [Agent gets body_text] → Tool 1 processes → sample

This is FASTER:
  - Agent can prefetch, filter, batch
  - Tool 1 doesn't re-fetch same document
  - Better for high-volume processing
    """)


def demo_glean_integration_code():
    """Show code example."""
    print_section("CODE EXAMPLE: INTEGRATION WITH GLEAN")

    code = '''
from google.cloud import bigquery
from tools import get_tool_by_name
import json

# Step 1: Get a candidate document from glean
bq_client = bigquery.Client()
query = """
    SELECT guid, title, body_text
    FROM glean.drive_files
    WHERE triage_category = 'INCL_STRUCTURED_RECORD'
    AND body_text IS NOT NULL
    LIMIT 1
"""

result = bq_client.query(query).result()
document = next(result)

# Step 2: Pass to Tool 1
tool1 = get_tool_by_name("fetch_and_sample")

input_to_tool1 = {
    "guid": document.guid,
    "body_text": document.body_text,
    "sample_size": 20,
    "find_header_heuristic": True,  # Smart header detection
}

response_json = tool1(input_to_tool1)
response = json.loads(response_json)

print(f"Document: {document.guid}")
print(f"Format detected: {response['detected_format_hint']}")
print(f"Headers at row: {response['actual_header_row_index']}")
print(f"Sample size: {response['sample_size']} rows")

# Step 3: Pass to Tool 2 (schema inference)
if response["status"] == "success":
    tool2 = get_tool_by_name("delimiter_detector")

    response2 = tool2({
        "raw_sample": response["raw_sample"],
        "detected_format_hint": response["detected_format_hint"],
        "actual_header_row_index": response["actual_header_row_index"],
    })

    print(f"Schema inferred: {response2['columns']} columns")
'''

    print(code)


def demo_response_format():
    """Show response structure."""
    print_section("RESPONSE FORMAT")

    print("""
When Tool 1 processes glean document:

{
  "status": "success",

  "guid": "ddffbdb6-5041-4d65-a744-5a0631a629aa",  ← NEW: Document reference
  "source_type": "glean_document",                   ← NEW: Source type
  "source_path": null,                               ← null for body_text input

  "raw_sample": "Location,Employee ID,...",
  "detected_format_hint": "csv",
  "first_line_is_header": true,
  "actual_header_row_index": 0,

  "total_bytes": 251117,
  "sample_size": 10,
  "byte_sample_size": 487,

  "encoding": "utf-8",
  "error": null,
  "timestamp": "2026-08-13T14:23:45.123456+00:00"
}

Key fields for Tool 2:
  ✓ raw_sample        - The structured data
  ✓ detected_format_hint - CSV/JSON/etc
  ✓ actual_header_row_index - Which row has headers
  ✓ guid              - Document reference
    """)


def demo_advantages():
    """Show advantages of new architecture."""
    print_section("ADVANTAGES OF THIS ARCHITECTURE")

    print("""
✓ Direct Integration with glean
  - Fetch document once, process multiple times
  - No re-reading from storage
  - Batch processing friendly

✓ Smart Header Detection
  - Handles payroll reports with metadata headers
  - Auto-detects when headers aren't on row 0
  - Works with comments/metadata sections

✓ Flexible Input
  - Accept pre-fetched body_text
  - Or fetch from path (backward compatible)
  - Supports all formats: CSV, JSON, pipe, tab

✓ Complete Metadata
  - guid: Track which document we're processing
  - source_type: Know the source
  - actual_header_row_index: Know where headers are
  - total_bytes: Know scope of data

✓ Deterministic Pipeline
  - Same input → Same output
  - No side effects (no database queries during processing)
  - Easy to test and debug
  - Can run in parallel on multiple documents

✓ PII-Safe
  - body_text is already in BigQuery
  - Not moving raw data around
  - Can be logged without data leaking
    """)


def main():
    """Run all demos."""
    print("\n" + "🔗 " * 35)
    print("GLEAN INTEGRATION WITH AGENTIC PIPELINE")
    print("🔗 " * 35)

    demo_glean_document_flow()
    demo_input_comparison()
    demo_glean_integration_code()
    demo_response_format()
    demo_advantages()

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print("""
Tool 1 now supports TWO input patterns:

1. FILE-BASED (backward compatible):
   {
     "source_path": "gs://bucket/file.csv"
   }

2. DOCUMENT-BASED (new, from glean):
   {
     "guid": "ddffbdb6-5041-4d65-a744-5a0631a629aa",
     "body_text": "CSV data here..."
   }

This enables the full agentic extraction flow:

glean.drive_files
    ↓ (Agent fetches documents)
Tool 1: fetch_and_sample
    ↓ (Returns sampled data + metadata)
Tool 2: delimiter_detector
    ↓ (Returns column schema)
Tool 3: generate_parser_script
    ↓ (Returns extraction code)
Tool 4: sandbox_run_and_evaluate
    ↓ (Returns validated results)
Tool 5: load_to_bigquery
    ↓ (Done!)

Ready to process any glean document with structured data!
    """)
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
