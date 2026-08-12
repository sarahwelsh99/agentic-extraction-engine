# Tool 1: fetch_and_sample - VALIDATION COMPLETE ✅

## Overview
**Purpose**: Fetch raw data from a source and return a small sample with metadata.

**Status**: ✅ READY FOR DEPLOYMENT

## Implementation Details

### Function Signature
```python
def fetch_and_sample(input_data: FetchAndSampleInput) -> str
```

### Input Schema
```python
class FetchAndSampleInput(TypedDict):
    source_path: str       # BigQuery table, GCS path, or local file
    sample_size: int       # Rows to sample (default: 10, max: 100)
    max_bytes: int         # Max bytes to fetch (default: 1MB, max: 10MB)
    skip_rows: int         # Rows to skip (default: 0)
    encoding: str          # Encoding hint (default: "utf-8")
```

### Output Schema (JSON)
```json
{
  "status": "success",
  "source_path": "string",
  "source_type": "bigquery|gcs|local_file",
  "total_rows": null,
  "total_bytes": 270,
  "sample_size": 5,
  "raw_sample": "string (raw data)",
  "encoding": "utf-8",
  "first_line_is_header": true,
  "detected_format_hint": "csv|json|pipe|tab|space_delimited|unknown",
  "byte_sample_size": 270,
  "error": null,
  "timestamp": "2026-08-12T21:10:08.314129+00:00"
}
```

## Test Results

### Unit Tests: 14/14 PASSED ✅
- `test_fetch_csv_file` ✓
- `test_fetch_with_skip_rows` ✓
- `test_fetch_nonexistent_file` ✓
- `test_fetch_missing_source_path` ✓
- `test_sample_size_capped` ✓
- `test_detect_common_header_keywords` ✓
- `test_detect_numeric_first_row` ✓
- `test_detect_empty_line` ✓
- `test_detect_csv` ✓
- `test_detect_json` ✓
- `test_detect_pipe_delimited` ✓
- `test_detect_tab_delimited` ✓
- `test_response_always_has_timestamp` ✓
- `test_response_status_field` ✓

### Validation Demo Results
- ✅ CSV file fetching: WORKING
- ✅ Format detection (CSV, pipe, tab, JSON): WORKING
- ✅ Error handling (missing path, nonexistent file): WORKING
- ✅ Header detection: WORKING
- ✅ Output format for next tool: CORRECT

## Features Implemented

### ✅ Data Source Support
- BigQuery tables (project.dataset.table format)
- GCS files (gs://bucket/path format)
- Local files (absolute paths)

### ✅ Format Detection
- Automatic detection of: CSV, JSON, pipe-delimited, tab-delimited, space-delimited
- Header row detection heuristics
- File encoding detection

### ✅ Error Handling
- Missing/invalid source paths
- Nonexistent files
- Parameter validation (sample_size capped at 100, etc.)
- Graceful error responses

### ✅ Response Consistency
- Always returns valid JSON
- Always includes `status`, `error`, and `timestamp` fields
- Standardized response structure for agent consumption

## Code Quality

- Type hints: ✅ Complete
- Docstrings: ✅ Comprehensive
- Error handling: ✅ Robust
- Tests: ✅ Comprehensive (14 test cases)
- Demo: ✅ Shows real-world usage

## Files Created

```
extraction/tools/
├── __init__.py
├── fetch_and_sample.py          (142 lines)
├── test_fetch_and_sample.py     (253 lines, 14 tests)
└── demo_fetch_and_sample.py     (146 lines)
```

## Next Steps

Tool 1 is complete and ready. The output it produces is perfect input for Tool 2.

**Tool 2**: `infer_schema_and_profile`
- Takes: `raw_sample` from Tool 1
- Does: Analyzes structure, detects data types, profiles columns
- Returns: Schema metadata for Tool 3

## How to Use Tool 1

### In Agent Loop
```python
from extraction.tools.fetch_and_sample import fetch_and_sample
import json

# Agent calls the tool
response_json = fetch_and_sample({
    "source_path": "project.dataset.table",
    "sample_size": 10
})

response = json.loads(response_json)
if response["status"] == "success":
    raw_data = response["raw_sample"]
    # Pass to next tool...
```

### Running Tests
```bash
python -m extraction.tools.test_fetch_and_sample
```

### Running Demo
```bash
python -m extraction.tools.demo_fetch_and_sample
```

## Ready to Build Tool 2?

Tool 1 is complete and validated. Ready to proceed to Tool 2 (infer_schema_and_profile)?
