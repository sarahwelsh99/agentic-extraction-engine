# Tools 1-3 Quick Reference Guide

## At a Glance

| Tool | Purpose | Input | Output | Dependencies |
|------|---------|-------|--------|--------------|
| **Tool 1** | Fetch & sample document | `guid` + `body_text` (optional) | Raw CSV sample + format detected | glean BigQuery |
| **Tool 2** | Infer schema & PII | Raw CSV sample + format hint | Column types + PII field mappings | mosaic schema (49 fields + 31 aliases) |
| **Tool 3** | Generate parser code | Columns schema + raw sample | Python extraction class | vLLM on localhost:8000 |

---

## Tool 1: fetch_and_sample

```
Input:
  {
    "guid": "ddffbdb6-5041-4d65-a744-5a0631a629aa",
    "body_text": "Employee ID,Name,Email\n10001,John,john@..."  [optional]
  }

Output:
  {
    "status": "success",
    "raw_sample": "Employee ID,Name,Email\n10001,John,john@...",
    "detected_format_hint": "csv",
    "actual_header_row_index": 0,
    "total_bytes": 289,
    "sample_size": 3
  }
```

**What it does:**
1. Fetches document from glean using mosaic's batch BigQuery queries
2. Detects CSV/JSON/TSV format from first few bytes
3. Identifies header row (usually row 0)
4. Returns N rows of data as raw text

**Key decisions:**
- Uses batch queries (1000 docs/query) for 100x speedup over per-doc queries
- Detects format by checking first line for common delimiters: `,` `|` `\t` ` `
- Limits sample to 5 rows by default (configurable)

---

## Tool 2: infer_schema_and_profile

```
Input:
  {
    "guid": "ddffbdb6-5041-4d65-a744-5a0631a629aa",
    "raw_sample": "Employee ID,Name,Email\n10001,John,john@...",
    "detected_format_hint": "csv",
    "actual_header_row_index": 0
  }

Output:
  {
    "status": "success",
    "columns": [
      {
        "name": "Employee ID",
        "detected_type": "integer",
        "pii_field": "PERSON_ID",           ← Maps to mosaic schema
        "pii_confidence": 0.85,
        "secondary_pii_fields": [          ← Paired ID type fields
          {
            "field": "PERSON_ID_TYPE",
            "inferred_value": "EMPLOYEE_ID",
            "confidence": 0.85
          }
        ],
        "sample_values": ["10001", "10002"],
        "patterns": ["currency"]           ← Detected patterns
      },
      ...
    ],
    "detected_schema": {
      "format": "csv",
      "total_columns": 5,
      "total_rows": 3,
      "pii_columns": 3                     ← How many have PII
    }
  }
```

**What it does:**
1. Parses CSV with proper quoted-field handling
2. Infers type for each column (integer, float, boolean, date, string)
3. Maps column names to 49 mosaic PII fields using:
   - Keyword matching (email → PERSON_EMAIL)
   - Alias lookup (ssn → PERSON_TAX_ID)
   - Pattern matching (regex for email, phone, SSN)
4. Detects patterns: email, phone, ssn, currency, date, uuid
5. Identifies paired ID fields (employee_id → PERSON_ID_TYPE="EMPLOYEE_ID")

**Key algorithms:**
- **Type inference:** Sample 10-50% of values, check numeric/boolean/date patterns
- **PII mapping:** Reverse lookup config.SCHEMA_ALIASES, then keyword match, then pattern match
- **ID type detection:** Look for "employee", "patient", etc. in column name → infer ID type

---

## Tool 3: generate_parser_script

```
Input:
  {
    "guid": "ddffbdb6-5041-4d65-a744-5a0631a629aa",
    "raw_sample": "Employee ID,Name,Email\n10001,John,john@...",
    "columns": [                           ← From Tool 2 output
      {"name": "Employee ID", "detected_type": "integer", "pii_field": "PERSON_ID", ...},
      ...
    ],
    "detected_schema": {                   ← From Tool 2 output
      "format": "csv", "total_columns": 5, "pii_columns": 3
    }
  }

Output:
  {
    "status": "success",
    "generated_code": {
      "language": "python",
      "code": "import csv\nimport re\nfrom typing import Dict, Any, List, Optional\n\nclass DataExtractor:\n    \"\"\"Extract and map data fields.\"\"\"\n    \n    FIELD_MAPPINGS = {\n        \"Employee ID\": \"PERSON_ID\",\n        \"Name\": \"PERSON_FULL_NAME\",\n        \"Email\": \"PERSON_EMAIL\"\n    }\n    \n    @classmethod\n    def extract(cls, file_path: str) -> List[Dict[str, Any]]:\n        \"\"\"Extract data from CSV.\"\"\"\n        # ... complete runnable code ...\n",
      "syntax_valid": true
    },
    "field_mappings": {
      "Employee ID": "PERSON_ID",
      "Name": "PERSON_FULL_NAME",
      "Email": "PERSON_EMAIL",
      "Salary": null
    },
    "extraction_rules": {
      "required_fields": ["Employee ID"],
      "nullable_fields": ["Salary"],
      "field_types": {"Employee ID": "integer", "Name": "string"},
      "validation_rules": {"Email": ["must_match_email_pattern"]}
    },
    "pii_extraction": {
      "pii_columns": 3,
      "mappings": [
        {"source_column": "Employee ID", "target_pii_field": "PERSON_ID", "confidence": 0.85}
      ]
    },
    "code_quality": {
      "has_type_hints": true,
      "has_error_handling": true,
      "has_validation": true,
      "has_documentation": true,
      "has_row_tracking": true,
      "generated_by": "vLLM"
    }
  }
```

**What it does:**
1. Builds prompt for vLLM with schema requirements
2. Calls vLLM (Qwen3-Coder-30B) on localhost:8000 with TP-4
3. Generates complete Python class with:
   - CSV parsing logic
   - Type conversion (integer, float, date, etc.)
   - Field validation (email, phone, SSN patterns)
   - Row tracking for error reporting
   - Error handling with try/except
4. Validates generated code syntax
5. Returns code + metadata about field mappings and validation rules

**Key features of generated code:**
- ✓ Type hints (`from typing import Dict, Any, List, Optional`)
- ✓ Error handling (`try/except` blocks)
- ✓ PII field validation (email, phone, SSN regex patterns)
- ✓ Row tracking (`_row_number` field in output)
- ✓ Docstrings for all methods
- ✓ Production-ready syntax

---

## Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                       DOCUMENT GUID                             │
│              ddffbdb6-5041-4d65-a744-5a0631a629aa              │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ↓
                    ┌─────────────────┐
                    │     TOOL 1      │
                    │ fetch_and_sample│
                    └────────┬────────┘
                             │
                ┌────────────┼────────────┐
                ↓            ↓            ↓
            raw_sample   format_hint   header_idx
         (CSV text)      ("csv")          (0)
                │            │            │
                └────────────┼────────────┘
                             │
                             ↓
                    ┌─────────────────┐
                    │     TOOL 2      │
                    │  infer_schema   │
                    └────────┬────────┘
                             │
         ┌───────────────────┼───────────────────┐
         ↓                   ↓                   ↓
      columns          detected_schema      pii_columns
    (w/ types)         (format metadata)      (count)
    (PII fields)
         │                   │                   │
         └───────────────────┼───────────────────┘
                             │
                             ↓
                    ┌─────────────────┐
                    │     TOOL 3      │
                    │  generate_code  │
                    └────────┬────────┘
                             │
           ┌─────────────────┼─────────────────┐
           ↓                 ↓                 ↓
    generated_code     field_mappings    extraction_rules
  (Python class)     (column → PII)     (validation logic)
           │                 │                 ↓
           │                 │            code_quality
           │                 │         (5 boolean flags)
           └─────────────────┼──────────────────┘
                             │
                             ↓
                   Python extraction code
                   ready for execution
```

---

## Key Concepts

### Type Detection
Tool 2 infers from sample values:
- **integer**: All values parseable as `int`
- **float**: Contains decimal points or scientific notation
- **boolean**: Values like true/false/yes/no/1/0
- **date**: Matches date patterns (MM/DD/YYYY, YYYY-MM-DD, etc.)
- **string**: Default fallback

### PII Field Mapping
Tool 2 maps to mosaic schema (49 fields) using:
1. **Keyword matching** (highest priority)
   - "email" → PERSON_EMAIL
   - "phone_number" → PERSON_PHONE_NUM
   - "ssn" → PERSON_TAX_ID
   
2. **Alias lookup** (31 configured aliases)
   - "dob" → PERSON_DATE_OF_BIRTH
   - "tin" → PERSON_TAX_ID
   
3. **Pattern matching** (regex)
   - Email: `^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$`
   - Phone: `^\d{3}-\d{3}-\d{4}$` or similar
   - SSN: `^\d{3}-\d{2}-\d{4}$`

### Paired ID Fields
When Tool 2 detects an ID field, it creates secondary mapping:
- Column name: "Employee ID" → Primary field: PERSON_ID
- Secondary field: PERSON_ID_TYPE = "EMPLOYEE_ID"
- Example:
  ```json
  {
    "pii_field": "PERSON_ID",
    "secondary_pii_fields": [
      {
        "field": "PERSON_ID_TYPE",
        "inferred_value": "EMPLOYEE_ID",
        "confidence": 0.85
      }
    ]
  }
  ```

---

## Error Handling

### Tool 1
- Network errors → Retry with exponential backoff
- Missing GUID → Returns error status
- Empty/invalid CSV → Returns what was found

### Tool 2
- Missing raw_sample → Returns error status
- Invalid CSV format → Falls back to line-by-line parsing
- Type inference ambiguous → Uses most likely type + lower confidence

### Tool 3
- vLLM unavailable → Returns error after 3 retries
- Generated code syntax error → Marked as `syntax_valid: false`
- Missing required input (columns) → Returns error status

---

## Performance Characteristics

| Tool | Time/Document | Bottleneck | Parallelizable |
|------|---------------|-----------|-----------------|
| Tool 1 | ~10-50ms | BigQuery batch fetch | Yes (96 workers) |
| Tool 2 | ~100-150ms | Type inference + PII matching | Yes (96 workers) |
| Tool 3 | ~1-2 seconds | vLLM inference latency | Yes (vLLM TP-4) |

**Total pipeline (4M documents, 96 workers):**
- Tool 1: ~30-45 minutes
- Tool 2: ~5-10 minutes  
- Tool 3: ~20-30 hours (vLLM bottleneck)
- **Total: ~20-31 hours for full pipeline**

---

## Testing

```bash
# Test each tool
python -m tools.fetch_and_sample.test_tool
python -m tools.infer_schema_and_profile.test_tool
python -m tools.generate_parser_script.test_tool

# Test end-to-end
python << 'EOF'
from tools import get_tool_by_name

tool1 = get_tool_by_name("fetch_and_sample")
tool2 = get_tool_by_name("infer_schema_and_profile")
tool3 = get_tool_by_name("generate_parser_script")

# Chain them on sample data
sample_csv = "ID,Name,Email\n1,John,john@example.com"
t1_out = tool1({"guid": "test", "body_text": sample_csv})
t2_out = tool2({"guid": "test", "raw_sample": t1_out["raw_sample"], ...})
t3_out = tool3({"guid": "test", "columns": t2_out["columns"], ...})

print(t3_out["generated_code"]["code"][:500])
EOF
```

---

## Real-World Examples

### Example 1: Payroll Report
```
Input: Payroll CSV with 41 columns
Tool 1 Output: CSV sample detected, 3 rows
Tool 2 Output: 41 columns → 15 PII fields identified
               Employee ID → PERSON_ID + PERSON_ID_TYPE
               Email → PERSON_EMAIL
               Salary → no PII
Tool 3 Output: 15,040 chars Python extraction class
               Field mappings for 41 columns
               Validation rules for email, integer types
```

### Example 2: Quality Rating Application
```
Input: Quality rating application CSV with 26 columns
Tool 1 Output: CSV sample detected, 3 rows
Tool 2 Output: 26 columns → 4 PII fields identified
               Contributor Name → PERSON_FULL_NAME
               Contributor Email → PERSON_EMAIL
               Job Title → JOB_TITLE
               (Low PII density - mostly assessment data)
Tool 3 Output: 5,543 chars Python extraction class
               Validation for name, email, job_title
```

